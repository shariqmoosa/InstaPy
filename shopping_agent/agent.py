"""Claude-powered shopping agent that extracts full pricing from online stores.

Token cost optimisations:
- Default model is claude-haiku-4-5 (5-10x cheaper than Sonnet, fast enough for navigation)
- System prompt and tools are marked cache_control=ephemeral so they are only billed
  once every 5 minutes instead of on every single API call
- Conversation history is trimmed to the last N turns to prevent unbounded growth
"""
import json
import os

import anthropic

from .browser import Browser
from .pricing import PricingResult
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

Food delivery platforms (Uber Eats, DoorDash, Grubhub, Deliveroo):
- Search for a specific item or use the provided URL
- Add it to cart — look for "Add to cart" or a "+" button
- At checkout the platform will show: subtotal, delivery fee, service fee, taxes, tip
- Enter a delivery address if prompted (use a generic city center address)
- Capture all line items before the final "Place Order" button

When you have collected all available pricing information, call extract_pricing with the
structured data, then provide a brief summary of what you found."""

# How many recent conversation turns to keep in history.
# Older turns are dropped to prevent token count growing without bound.
_MAX_HISTORY_TURNS = 12

# Approximate token cost table for informational logging (per 1M tokens, USD)
_COST_PER_MTK = {
    "claude-haiku-4-5":    {"input": 0.80,  "output": 4.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6":   {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":     {"input": 15.00, "output": 75.00},
}


def _estimate_cost(model, input_tokens, output_tokens):
    rates = _COST_PER_MTK.get(model, {"input": 3.00, "output": 15.00})
    return (input_tokens / 1_000_000) * rates["input"] + \
           (output_tokens / 1_000_000) * rates["output"]


def _cached(text):
    """Wrap a text string as a content block with prompt caching enabled."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def _cached_tools():
    """Return TOOL_SCHEMAS with cache_control on the last tool (caches the whole list)."""
    if not TOOL_SCHEMAS:
        return TOOL_SCHEMAS
    tools = list(TOOL_SCHEMAS)
    last = dict(tools[-1])
    last["cache_control"] = {"type": "ephemeral"}
    tools[-1] = last
    return tools


class ShoppingAgent:
    def __init__(
        self,
        api_key=None,
        headless=True,
        max_iterations=25,
        model="claude-haiku-4-5",   # cheapest capable model by default
        cookie_dir=None,
        verbose=True,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required (pass api_key or set env var)")
        self.headless = headless
        self.max_iterations = max_iterations
        self.model = model
        self.cookie_dir = cookie_dir
        self.verbose = verbose
        self._client = anthropic.Anthropic(api_key=self.api_key)
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_pricing(self, product_url, zip_code=None):
        """
        Navigate to a product, add to cart, and extract full pricing breakdown.
        Loads saved cookies for the domain to appear as a returning user.
        Saves cookies after the session for future runs.

        Args:
            product_url: URL of the product page
            zip_code: Optional ZIP/postal code for shipping calculation

        Returns:
            PricingResult with full pricing breakdown
        """
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        browser = Browser(headless=self.headless, cookie_dir=self.cookie_dir)
        browser.start()

        try:
            had_cookies = browser.navigate_with_cookies(product_url)
            if self.verbose:
                status = "returning user (cookies loaded)" if had_cookies else "fresh session"
                print(f"[ShoppingAgent] Starting — {status} — model={self.model}")

            result = self._run_agent(browser, product_url, zip_code)
        finally:
            try:
                browser.save_cookies(product_url)
            except Exception:
                pass
            browser.quit()

        if self.verbose:
            cost = _estimate_cost(
                self.model, self._total_input_tokens, self._total_output_tokens
            )
            print(
                f"[ShoppingAgent] Done — "
                f"tokens in={self._total_input_tokens:,} out={self._total_output_tokens:,} "
                f"estimated_cost=${cost:.4f}"
            )

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_agent(self, browser, product_url, zip_code):
        user_message = (
            f"The browser is already on {product_url} with cookies loaded. "
            f"Get the full pricing breakdown — add the item to cart and proceed "
            f"through checkout to reveal all fees."
        )
        if zip_code:
            user_message += f"\nUse ZIP code {zip_code} for shipping calculation."

        messages = [{"role": "user", "content": user_message}]
        pricing_result = None

        for iteration in range(self.max_iterations):
            if self.verbose:
                print(f"[ShoppingAgent] Iteration {iteration + 1}/{self.max_iterations}")

            response = self._client.messages.create(
                model=self.model,
                max_tokens=2048,
                # Cache the system prompt — re-used every call, billed once per 5 min
                system=[_cached(SYSTEM_PROMPT)],
                # Cache the tool list — large and static
                tools=_cached_tools(),
                messages=self._trim_history(messages),
            )

            # Track token usage
            u = response.usage
            self._total_input_tokens += u.input_tokens
            self._total_output_tokens += u.output_tokens
            cache_saved = getattr(u, "cache_read_input_tokens", 0)
            if self.verbose and cache_saved:
                print(f"[ShoppingAgent]   cache_hit={cache_saved:,} tokens saved")

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                final_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text = block.text
                        break
                if pricing_result:
                    pricing_result.notes = (pricing_result.notes + " " + final_text).strip()
                else:
                    pricing_result = PricingResult(store_url=product_url, notes=final_text)
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool_name = block.name
                    tool_input = block.input
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
                        "tool_use_id": block.id,
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
        """
        Keep only the first message (task description) + the last N turns.
        Prevents the prompt from growing unboundedly across many iterations.
        """
        if len(messages) <= _MAX_HISTORY_TURNS + 1:
            return messages
        return [messages[0]] + messages[-(  _MAX_HISTORY_TURNS):]

    @property
    def estimated_cost(self):
        """Return estimated USD cost for the last run."""
        return _estimate_cost(
            self.model, self._total_input_tokens, self._total_output_tokens
        )
