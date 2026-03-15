"""Tool definitions and handlers for the Claude shopping agent."""
import json

# Phrases that indicate a final purchase button — never click these
_DANGER_PHRASES = [
    "place order", "place your order", "confirm order", "confirm purchase",
    "submit order", "complete purchase", "complete order",
    "pay now", "confirm and pay", "confirm & pay",
    "submit payment", "buy now", "purchase now",
]

TOOL_SCHEMAS = [
    {
        "name": "navigate_to_url",
        "description": "Navigate the browser to a URL. Use this to go to product pages, cart, or checkout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full URL to navigate to"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_page_content",
        "description": (
            "Get the current page's visible text content and current URL. "
            "Also reports if the page is a bot-block or CAPTCHA page. "
            "Use this to read product names, prices, form fields, and checkout summaries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "find_elements",
        "description": (
            "Find elements on the page matching a CSS selector or XPath expression. "
            "Returns up to 20 elements with their text, href, id, class, and tag. "
            "Useful for finding buttons, links, inputs, and price elements."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector or XPath expression",
                },
                "by_xpath": {
                    "type": "boolean",
                    "description": "If true, treat selector as XPath. Default false (CSS).",
                    "default": False,
                },
            },
            "required": ["selector"],
        },
    },
    {
        "name": "click_element",
        "description": (
            "Click an element identified by a CSS selector or XPath. "
            "Use for buttons like 'Add to Cart', 'Proceed to Checkout', 'Continue', etc. "
            "Will refuse to click any button that would complete a purchase (Place Order, Pay Now, etc.)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector or XPath for the element to click",
                },
                "by_xpath": {
                    "type": "boolean",
                    "description": "If true, treat selector as XPath. Default false (CSS).",
                    "default": False,
                },
            },
            "required": ["selector"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into a form field (input or textarea) identified by a CSS selector or XPath.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector or XPath for the input field",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type into the field",
                },
                "by_xpath": {
                    "type": "boolean",
                    "description": "If true, treat selector as XPath. Default false (CSS).",
                    "default": False,
                },
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "scroll_page",
        "description": (
            "Scroll the page to reveal lazy-loaded content or bring an element into view. "
            "Use 'bottom' to scroll to end of page, 'top' to return to top, "
            "'element' + selector to scroll a specific element into view."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["bottom", "top", "element"],
                    "description": "Where to scroll",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector — required only when direction='element'",
                },
            },
            "required": ["direction"],
        },
    },
    {
        "name": "extract_pricing",
        "description": (
            "Extract and structure the full pricing breakdown from the current page. "
            "Use this when you can see the order summary with line items like "
            "item price, shipping, tax, fees, and total. "
            "Provide the raw pricing text you see on the page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": {
                    "type": "string",
                    "description": "Name of the product",
                },
                "base_price": {
                    "type": "number",
                    "description": "Item/product price (numeric, no currency symbol)",
                },
                "shipping": {
                    "type": "number",
                    "description": "Shipping cost (0 if free, null if unknown)",
                },
                "tax": {
                    "type": "number",
                    "description": "Tax amount (null if not shown)",
                },
                "service_fee": {
                    "type": "number",
                    "description": "Service or platform fee (null if none)",
                },
                "other_fees": {
                    "type": "object",
                    "description": "Any other fee line items as {name: amount}",
                    "additionalProperties": {"type": "number"},
                },
                "total": {
                    "type": "number",
                    "description": "Grand total (null if not shown)",
                },
                "currency": {
                    "type": "string",
                    "description": "Currency code, e.g. USD, EUR, GBP",
                    "default": "USD",
                },
                "notes": {
                    "type": "string",
                    "description": "Any additional observations about the pricing",
                },
            },
            "required": ["product_name"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the current page for visual inspection.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def _is_dangerous_button(browser, selector, by_xpath=False):
    """Return True if the element text matches a final-purchase phrase."""
    elements = browser.find_elements(selector, by_xpath=by_xpath)
    for el in elements:
        text = el.get("text", "").lower().strip()
        if any(phrase in text for phrase in _DANGER_PHRASES):
            return True
    return False


def handle_tool(name, tool_input, browser):
    """Dispatch a tool call to the browser and return a string result."""
    if name == "navigate_to_url":
        url = tool_input["url"]
        browser.navigate(url)
        current = browser.get_current_url()
        blocked = browser.is_blocked()
        if blocked:
            return (
                f"Navigated to: {current}\n"
                "WARNING: Page appears to be a bot-block or CAPTCHA page. "
                "The site may have detected automation. Try scrolling or waiting."
            )
        return f"Navigated to: {current}"

    elif name == "get_page_content":
        text = browser.get_page_text()
        url = browser.get_current_url()
        blocked = browser.is_blocked()
        if len(text) > 6000:
            text = text[:6000] + "\n...[truncated]"
        prefix = "BLOCKED: Bot/CAPTCHA page detected.\n\n" if blocked else ""
        return f"{prefix}URL: {url}\n\n{text}"

    elif name == "find_elements":
        selector = tool_input["selector"]
        by_xpath = tool_input.get("by_xpath", False)
        elements = browser.find_elements(selector, by_xpath=by_xpath)
        if not elements:
            return "No elements found matching the selector."
        lines = []
        for i, el in enumerate(elements):
            parts = [f"[{i}] tag={el['tag']}"]
            if el["text"]:
                parts.append(f"text={el['text']!r}")
            if el["href"]:
                parts.append(f"href={el['href']}")
            if el["id"]:
                parts.append(f"id={el['id']!r}")
            if el["value"]:
                parts.append(f"value={el['value']!r}")
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    elif name == "click_element":
        selector = tool_input["selector"]
        by_xpath = tool_input.get("by_xpath", False)
        # Safety: block any final purchase button
        if _is_dangerous_button(browser, selector, by_xpath=by_xpath):
            return (
                "BLOCKED: This element appears to be a final purchase/payment button. "
                "Not clicking it to avoid completing a real order. "
                "Extract pricing from what is visible and call extract_pricing."
            )
        result = browser.click(selector, by_xpath=by_xpath)
        if result is True:
            url = browser.get_current_url()
            blocked = browser.is_blocked()
            msg = f"Clicked successfully. Current URL: {url}"
            if blocked:
                msg += "\nWARNING: Page appears to be a bot-block or CAPTCHA page."
            return msg
        return str(result)

    elif name == "type_text":
        selector = tool_input["selector"]
        text = tool_input["text"]
        by_xpath = tool_input.get("by_xpath", False)
        result = browser.type_text(selector, text, by_xpath=by_xpath)
        if result is True:
            return f"Typed {text!r} into field."
        return str(result)

    elif name == "scroll_page":
        direction = tool_input.get("direction", "bottom")
        selector = tool_input.get("selector")
        browser.scroll(direction=direction, selector=selector)
        return f"Scrolled {direction}."

    elif name == "extract_pricing":
        # Handled in agent.py — just echo back for the tool_result
        return json.dumps(tool_input)

    elif name == "take_screenshot":
        data = browser.screenshot()
        if isinstance(data, str) and data.startswith("Screenshot failed"):
            return data
        return f"Screenshot captured ({len(data)} bytes base64)."

    return f"Unknown tool: {name}"
