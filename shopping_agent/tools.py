"""Tool definitions and handlers for the Claude shopping agent."""

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
            "Use for buttons like 'Add to Cart', 'Proceed to Checkout', 'Continue', etc."
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


def handle_tool(name, tool_input, browser):
    """Dispatch a tool call to the browser and return a string result."""
    if name == "navigate_to_url":
        url = tool_input["url"]
        browser.navigate(url)
        current = browser.get_current_url()
        return f"Navigated to: {current}"

    elif name == "get_page_content":
        text = browser.get_page_text()
        url = browser.get_current_url()
        # Truncate to avoid huge context
        if len(text) > 6000:
            text = text[:6000] + "\n...[truncated]"
        return f"URL: {url}\n\n{text}"

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
        result = browser.click(selector, by_xpath=by_xpath)
        if result is True:
            url = browser.get_current_url()
            return f"Clicked successfully. Current URL: {url}"
        return str(result)

    elif name == "type_text":
        selector = tool_input["selector"]
        text = tool_input["text"]
        by_xpath = tool_input.get("by_xpath", False)
        result = browser.type_text(selector, text, by_xpath=by_xpath)
        if result is True:
            return f"Typed {text!r} into field."
        return str(result)

    elif name == "extract_pricing":
        # This tool's result is handled specially in agent.py — we just echo it back
        import json
        return json.dumps(tool_input)

    elif name == "take_screenshot":
        data = browser.screenshot()
        if data.startswith("Screenshot failed"):
            return data
        return f"Screenshot captured ({len(data)} bytes base64)."

    return f"Unknown tool: {name}"
