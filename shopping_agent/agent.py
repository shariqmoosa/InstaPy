"""Claude-powered shopping agent that extracts full pricing from online stores.

Provider-agnostic: works with Claude, OpenAI, or Gemini via models.py.

Token cost optimisations:
- Default model is claude-haiku-4-5 (cheapest capable model)
- System prompt and tools are cached (Claude only) — billed once per 5 min
- Conversation history is trimmed to prevent unbounded growth
"""
import json
import os

from .browser import Browser
from .models import get_model_client, estimate_cost
from .pricing import PricingResult
from .vpn import AccountManager
from .tools import TOOL_SCHEMAS, handle_tool

SYSTEM_PROMPT = """You are a shopping price researcher. Your job is to:
1. Navigate to a product or menu item page
2. Add the item to the cart/basket
3. Proceed through checkout as far as needed to reveal ALL fees:
   - Delivery / shipping cost
   - Taxes
   - Service charges, platform fees, small order fees
   - Any other hidden fees
4. Once you can see the full order summary with all line items, call extract_pricing
5. NEVER click "Place Order", "Buy Now", "Submit Payment", "Confirm Order",
   "Place your order", "Checkout", or any button that would complete a real purchase

Strategy tips:
- After adding to cart, look for a "Proceed to Checkout", "Go to Cart", or "View Basket" button
- At checkout, you may need to enter a delivery address or ZIP/postal code to trigger fee calculation
- Look for an order summary section showing itemized costs
- If a site asks you to sign in, try to proceed as guest; if guest checkout isn't available,
  extract whatever fees are shown before the login wall
- Use get_page_content to read the page, find_elements to locate specific buttons/inputs
- Use scroll_page when content may be below the fold
- If get_page_content reports BLOCKED or CAPTCHA, call take_screenshot and report the issue

Food delivery platforms (Uber Eats, DoorDash, Grubhub, Instacart, Deliveroo):
- Search for a specific item or use the provided URL
- Add it to cart — look for "Add to cart" or a "+" button
- At checkout the platform will show: subtotal, delivery fee, service fee, taxes, tip
- Enter a delivery address if prompted (use a generic city center address)
- Capture all line items before the final "Place Order" button

When you have collected all available pricing information, call extract_pricing with the
structured data, then provide a brief summary of what you found."""

_MAX_HISTORY_TURNS = 12


class ShoppingAgent:
    def __init__(
        self,
        provider="auto",
        api_key=None,
        model=None,           # None = use provider default (haiku / gpt-4o-mini / gemini-flash)
        headless=True,
        max_iterations=25,
        cookie_dir=None,
        verbose=True,
        account=None,         # Named account for IP switching via Surfshark
    ):
        """
        Args:
            provider:   "claude" | "openai" | "gemini" | "auto"
            api_key:    API key for the chosen provider
            model:      Model override e.g. "claude-sonnet-4-6", "gpt-4o", "gemini-1.5-pro"
                        Defaults: Claude→haiku-4-5, OpenAI→gpt-4o-mini, Gemini→gemini-1.5-flash
            headless:   Run browser without visible window
            max_iterations: Safety cap on agentic loop
            cookie_dir: Where to save/load cookies (default: ~/.shopping_agent/cookies)
            verbose:    Print progress logs
            account:    Named account profile (see `account add` command). When set,
                        the browser routes traffic through that account's Surfshark
                        proxy and uses a namespaced cookie directory so each account
                        maintains its own login session.
        """
        self._llm = get_model_client(provider=provider, api_key=api_key, model=model)
        self.headless = headless
        self.max_iterations = max_iterations
        self.verbose = verbose
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        # Resolve account → proxy + per-account cookie directory
        self._proxy = None
        if account:
            mgr = AccountManager()
            self._proxy = mgr.get(account)
            if self._proxy is None:
                raise ValueError(
                    f"Account '{account}' not found. "
                    "Run: python -m shopping_agent.main account add <name> <location>"
                )
            if verbose:
                print(f"[ShoppingAgent] Account '{account}' → proxy {self._proxy.host}")
            # Namespace cookies per account so sessions don't bleed across profiles
            base = cookie_dir or os.path.join(
                os.path.expanduser("~"), ".shopping_agent", "cookies"
            )
            self.cookie_dir = os.path.join(base, account)
        else:
            self.cookie_dir = cookie_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_pricing(self, product_url, zip_code=None, address=None):
        """
        Navigate to a product, add to cart, and extract full pricing breakdown.

        Args:
            product_url: URL of the product page
            zip_code:    Optional ZIP/postal code for shipping calculation
            address:     Optional delivery address string

        Returns:
            PricingResult
        """
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        browser = Browser(headless=self.headless, cookie_dir=self.cookie_dir, proxy=self._proxy)
        browser.start()

        try:
            had_cookies = browser.navigate_with_cookies(product_url)
            if self.verbose:
                status = "cookies loaded" if had_cookies else "fresh session"
                print(f"[ShoppingAgent] {self._llm.__class__.__name__} | {status} | {product_url[:60]}")

            result = self._run_agent(browser, product_url, zip_code, address)
        finally:
            try:
                browser.save_cookies(product_url)
            except Exception:
                pass
            browser.quit()

        if self.verbose:
            cost = estimate_cost(
                getattr(self._llm, "model", "unknown"),
                self._total_input_tokens,
                self._total_output_tokens,
            )
            print(
                f"[ShoppingAgent] Done | "
                f"in={self._total_input_tokens:,} out={self._total_output_tokens:,} "
                f"~${cost:.4f}"
            )

        return result

    def compare(self, stores, zip_code=None, address=None):
        """
        Run get_pricing against multiple store URLs and return a comparison.

        Args:
            stores:   list of URLs  OR  dict of {label: url}
            zip_code: ZIP code for all stores
            address:  Delivery address for all stores

        Returns:
            list of {"store": label, "url": url, "result": PricingResult}
        """
        if isinstance(stores, dict):
            items = list(stores.items())
        else:
            items = [(url, url) for url in stores]

        results = []
        for label, url in items:
            if self.verbose:
                print(f"\n{'='*50}\n[Compare] {label}\n{'='*50}")
            try:
                result = self.get_pricing(url, zip_code=zip_code, address=address)
            except Exception as e:
                result = PricingResult(store_url=url, notes=f"Error: {e}")
            results.append({"store": label, "url": url, "result": result})

        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_agent(self, browser, product_url, zip_code, address):
        parts = [
            f"The browser is already on {product_url} with cookies loaded.",
            "Get the full pricing breakdown — add the item to cart and proceed "
            "through checkout to reveal all fees (delivery, service fees, taxes, etc.).",
        ]
        if address:
            parts.append(f"Use this delivery address: {address}")
        elif zip_code:
            parts.append(f"Use ZIP code {zip_code} for shipping/delivery calculation.")

        messages = [{"role": "user", "content": " ".join(parts)}]
        pricing_result = None

        for iteration in range(self.max_iterations):
            if self.verbose:
                print(f"[ShoppingAgent]   iter {iteration+1}/{self.max_iterations}")

            response = self._llm.chat(
                messages=self._trim_history(messages),
                tools=TOOL_SCHEMAS,
                system_prompt=SYSTEM_PROMPT,
            )

            self._total_input_tokens += response.input_tokens
            self._total_output_tokens += response.output_tokens
            if self.verbose and response.cache_hit_tokens:
                print(f"[ShoppingAgent]   cache_hit={response.cache_hit_tokens:,} tokens")

            # Store assistant response — keep raw content for history
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                final_text = response.text()
                if pricing_result:
                    pricing_result.notes = (pricing_result.notes + " " + final_text).strip()
                else:
                    pricing_result = PricingResult(store_url=product_url, notes=final_text)
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.tool_calls():
                    tool_name = block["name"]
                    tool_input = block["input"]
                    tool_id = block["id"]

                    if self.verbose:
                        print(f"[ShoppingAgent]   → {tool_name}")

                    if tool_name == "extract_pricing":
                        pricing_result = PricingResult(
                            product_name=tool_input.get("product_name", ""),
                            base_price=tool_input.get("base_price"),
                            shipping=tool_input.get("shipping"),
                            tax=tool_input.get("tax"),
                            service_fee=tool_input.get("service_fee"),
                            other_fees=tool_input.get("other_fees") or {},
                            total=tool_input.get("total"),
                            currency=tool_input.get("currency", "USD"),
                            store_url=product_url,
                            notes=tool_input.get("notes", ""),
                        )
                        result_str = "Pricing extracted successfully."
                    else:
                        result_str = handle_tool(tool_name, tool_input, browser)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_str,
                    })

                messages.append({"role": "user", "content": tool_results})

        if pricing_result is None:
            pricing_result = PricingResult(
                store_url=product_url,
                notes="Agent reached iteration limit without extracting pricing.",
            )

        return pricing_result

    def _trim_history(self, messages):
        if len(messages) <= _MAX_HISTORY_TURNS + 1:
            return messages
        return [messages[0]] + messages[-_MAX_HISTORY_TURNS:]

    @property
    def estimated_cost(self):
        return estimate_cost(
            getattr(self._llm, "model", "unknown"),
            self._total_input_tokens,
            self._total_output_tokens,
        )
