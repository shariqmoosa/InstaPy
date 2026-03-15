"""Claude-powered shopping agent that extracts full pricing from online stores."""
import json
import os

import anthropic

from .browser import Browser
from .pricing import PricingResult
from .tools import TOOL_SCHEMAS, handle_tool

SYSTEM_PROMPT = """You are a shopping price researcher. Your job is to:
1. Navigate to a product page
2. Add the item to the cart
3. Proceed through checkout as far as needed to reveal ALL fees:
   - Shipping cost
   - Taxes
   - Service charges
   - Any other hidden fees
4. Once you can see the full order summary with all line items, call extract_pricing
5. NEVER click "Place Order", "Buy Now", "Submit Payment", "Confirm Order", or any button that would complete a purchase

Strategy tips:
- After adding to cart, look for a "Proceed to Checkout" or "View Cart" button
- At checkout, you may need to enter a ZIP/postal code to get shipping rates
- Look for an order summary section showing itemized costs
- If a site asks you to sign in, try to proceed as guest
- If checkout requires an account, extract whatever pricing is visible in the cart
- Use get_page_content to read the page, find_elements to locate specific buttons/inputs

When you have collected all available pricing information, call extract_pricing with the
structured data, then provide a brief summary of what you found."""


class ShoppingAgent:
    def __init__(self, api_key=None, headless=True, max_iterations=25, model="claude-sonnet-4-6"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required (pass api_key or set env var)")
        self.headless = headless
        self.max_iterations = max_iterations
        self.model = model
        self._client = anthropic.Anthropic(api_key=self.api_key)

    def get_pricing(self, product_url, zip_code=None):
        """
        Navigate to a product, add to cart, and extract full pricing breakdown.

        Args:
            product_url: URL of the product page
            zip_code: Optional ZIP/postal code for shipping calculation

        Returns:
            PricingResult with full pricing breakdown
        """
        browser = Browser(headless=self.headless)
        browser.start()

        try:
            return self._run_agent(browser, product_url, zip_code)
        finally:
            browser.quit()

    def _run_agent(self, browser, product_url, zip_code):
        user_message = f"Get full pricing breakdown for this product: {product_url}"
        if zip_code:
            user_message += f"\nUse ZIP code {zip_code} for shipping calculation."

        messages = [{"role": "user", "content": user_message}]
        pricing_result = None

        for iteration in range(self.max_iterations):
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            # Append assistant response to history
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Agent is done — extract final text summary
                final_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text = block.text
                        break
                if pricing_result:
                    pricing_result.notes = (pricing_result.notes + " " + final_text).strip()
                else:
                    pricing_result = PricingResult(
                        store_url=product_url,
                        notes=final_text,
                    )
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool_name = block.name
                    tool_input = block.input

                    if tool_name == "extract_pricing":
                        # Build PricingResult from the structured input
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
