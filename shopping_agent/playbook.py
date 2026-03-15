"""
Playbook runner — loads a recorded playbook and replays it against one or
more store URLs, extracting pricing at the end.

Replay strategy per step (most reliable → least):
  1. CSS selector (exact)
  2. XPath
  3. Text search (find button/link containing the recorded text)
  4. Screen coordinates (absolute fallback)

Usage:
    from shopping_agent.playbook import PlaybookRunner

    runner = PlaybookRunner()

    # Single store
    result = runner.run("https://www.ubereats.com/store/kfc/xyz")

    # Multiple stores using the same recorded playbook pattern
    stores = [
        "https://www.ubereats.com/store/mcdonalds/abc",
        "https://www.ubereats.com/store/kfc/xyz",
        "https://www.ubereats.com/store/subway/123",
    ]
    results = runner.run_many(stores, playbook_domain="ubereats.com")
"""
import json
import os
import time
import random
from urllib.parse import urlparse

from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, WebDriverException,
    ElementClickInterceptedException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .browser import Browser, _domain_from_url, _human_delay
from .pricing import PricingResult

_DEFAULT_PLAYBOOK_DIR = os.path.join(os.path.expanduser("~"), ".shopping_agent", "playbooks")

# Selectors that reliably identify an order/price summary across most stores
_PRICE_SUMMARY_SELECTORS = [
    # Generic
    "[class*='order-summary']", "[class*='price-summary']",
    "[class*='cart-summary']", "[class*='checkout-summary']",
    "[id*='order-summary']", "[id*='price-summary']",
    # Uber Eats / DoorDash
    "[data-testid*='summary']", "[data-testid*='price']",
    # Amazon
    "#subtotals-marketplace-table", "#order-summary",
    # eBay
    ".order-summary",
    # Etsy
    ".order-summary-price",
]

# Regex-ish patterns for extracting price line items from page text
import re
_PRICE_LINE_PATTERN = re.compile(
    r"(subtotal|item|items|delivery|shipping|service fee|service charge|"
    r"taxes?|vat|gst|hst|tip|total|grand total|estimated total|small order fee|"
    r"bag fee|platform fee|processing fee)"
    r"[^\d\n]{0,30}"
    r"(free|[\$£€¥₹]\s*[\d,]+\.?\d*)",
    re.IGNORECASE,
)


def _parse_amount(raw):
    """Convert '$12.99' or 'FREE' to float."""
    raw = raw.strip().lower()
    if raw in ("free", "free shipping", "free delivery"):
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


class PlaybookRunner:
    """
    Replays a recorded playbook against one or more store URLs.
    Falls back through multiple element-finding strategies before using coordinates.
    """

    def __init__(self, playbook_dir=None, cookie_dir=None, headless=True, verbose=True):
        self.playbook_dir = playbook_dir or _DEFAULT_PLAYBOOK_DIR
        self.cookie_dir = cookie_dir
        self.headless = headless
        self.verbose = verbose

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, url, playbook_domain=None):
        """
        Replay the playbook for the given URL and return a PricingResult.

        Args:
            url: Product/store URL to run against
            playbook_domain: Override which playbook to use (e.g. 'ubereats.com').
                             Defaults to the domain of url.
        """
        domain = playbook_domain or _domain_from_url(url)
        playbook = self._load_playbook(domain)
        if not playbook:
            raise FileNotFoundError(
                f"No playbook found for '{domain}'. "
                f"Record one first with SiteRecorder."
            )

        browser = Browser(headless=self.headless, cookie_dir=self.cookie_dir)
        browser.start()
        try:
            browser.navigate_with_cookies(url)
            result = self._replay(browser, url, playbook)
        finally:
            try:
                browser.save_cookies(url)
            except Exception:
                pass
            browser.quit()

        return result

    def run_many(self, urls, playbook_domain=None):
        """
        Run the same playbook against multiple store URLs.
        Returns list of (url, PricingResult) tuples.
        """
        results = []
        for url in urls:
            if self.verbose:
                print(f"\n[PlaybookRunner] ── Running {url}")
            try:
                result = self.run(url, playbook_domain=playbook_domain)
                results.append((url, result))
                if self.verbose:
                    print(f"[PlaybookRunner] ✓ {url} → total={result.total}")
            except Exception as e:
                if self.verbose:
                    print(f"[PlaybookRunner] ✗ {url} → {e}")
                results.append((url, PricingResult(store_url=url, notes=str(e))))
        return results

    def list_playbooks(self):
        result = []
        for fname in os.listdir(self.playbook_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.playbook_dir, fname)
                try:
                    with open(path) as f:
                        data = json.load(f)
                    result.append({
                        "store": data.get("store"),
                        "description": data.get("description", ""),
                        "recorded_at": data.get("recorded_at", ""),
                        "steps": len(data.get("steps", [])),
                        "path": path,
                    })
                except Exception:
                    pass
        return result

    # ------------------------------------------------------------------
    # Replay engine
    # ------------------------------------------------------------------

    def _replay(self, browser, base_url, playbook):
        steps = playbook.get("steps", [])
        recorded_base = playbook.get("base_url", "")
        pricing_result = PricingResult(store_url=base_url)

        for i, step in enumerate(steps):
            stype = step.get("type")
            label = step.get("label", f"step {i}")
            if self.verbose:
                print(f"[PlaybookRunner]   [{i+1}/{len(steps)}] {label}")

            if stype == "navigate":
                target_url = self._remap_url(step["url"], recorded_base, base_url)
                browser.navigate(target_url)

            elif stype == "click":
                ok = self._smart_click(browser, step)
                if not ok and self.verbose:
                    print(f"[PlaybookRunner]   ⚠ click failed for: {label}")

            elif stype == "input":
                self._smart_type(browser, step)

            elif stype == "wait":
                time.sleep(step.get("ms", 1000) / 1000)

            elif stype == "extract_pricing":
                # Explicit extraction step recorded by user
                self._extract_into(browser, pricing_result)

            # After every step, try a quick price scan in the background
            self._try_extract(browser, pricing_result)

        # Final extraction pass on whatever page we ended up on
        self._extract_into(browser, pricing_result)
        return pricing_result

    def _smart_click(self, browser, step):
        """Try CSS → XPath → text search → coordinates."""
        driver = browser._driver

        # 1. CSS selector
        sel = step.get("selector", "")
        if sel and sel != "body":
            try:
                el = WebDriverWait(driver, 6).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                _human_delay(200, 500)
                ActionChains(driver).move_to_element(el).perform()
                _human_delay(80, 200)
                el.click()
                _human_delay(500, 1200)
                return True
            except Exception:
                pass

        # 2. XPath
        xpath = step.get("xpath", "")
        if xpath:
            try:
                el = WebDriverWait(driver, 4).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                _human_delay(200, 500)
                el.click()
                _human_delay(500, 1200)
                return True
            except Exception:
                pass

        # 3. Text-based search
        text = step.get("text", "").strip()
        if text and len(text) > 2:
            tag = step.get("tag", "button")
            text_xpath = (
                f"//{tag}[contains(normalize-space(.), "
                f"{json.dumps(text[:50])})]"
            )
            try:
                el = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, text_xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                _human_delay(200, 400)
                el.click()
                _human_delay(500, 1200)
                return True
            except Exception:
                pass

        # 4. Screen coordinates (last resort)
        coords = step.get("coords", {})
        if coords.get("x") and coords.get("y"):
            try:
                ActionChains(driver).move_by_offset(
                    coords["x"], coords["y"]
                ).click().perform()
                ActionChains(driver).move_by_offset(
                    -coords["x"], -coords["y"]
                ).perform()  # reset offset
                _human_delay(500, 1000)
                return True
            except Exception:
                pass

        return False

    def _smart_type(self, browser, step):
        """Type into a field using CSS → XPath fallback."""
        driver = browser._driver
        value = step.get("value", "")
        sel = step.get("selector", "")

        for by, locator in [
            (By.CSS_SELECTOR, sel),
            (By.XPATH, step.get("xpath", "")),
        ]:
            if not locator:
                continue
            try:
                el = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((by, locator))
                )
                ActionChains(driver).move_to_element(el).perform()
                _human_delay(100, 300)
                el.click()
                el.clear()
                # Slow human-like typing
                for char in value:
                    el.send_keys(char)
                    time.sleep(random.uniform(0.04, 0.15))
                _human_delay(200, 400)
                return True
            except Exception:
                continue
        return False

    def _try_extract(self, browser, result):
        """Quick non-blocking attempt to find pricing on the current page."""
        try:
            text = browser.get_page_text()
            matches = _PRICE_LINE_PATTERN.findall(text)
            for label, raw in matches:
                amount = _parse_amount(raw)
                if amount is None:
                    continue
                label_lower = label.lower()
                if any(k in label_lower for k in ("subtotal", "item")):
                    if result.base_price is None:
                        result.base_price = amount
                elif any(k in label_lower for k in ("delivery", "shipping")):
                    result.shipping = amount
                elif any(k in label_lower for k in ("service fee", "service charge", "platform fee", "processing fee")):
                    result.service_fee = amount
                elif any(k in label_lower for k in ("tax", "vat", "gst", "hst")):
                    result.tax = amount
                elif any(k in label_lower for k in ("total", "grand total", "estimated total")):
                    result.total = amount
                elif any(k in label_lower for k in ("tip", "bag fee", "small order fee")):
                    key = label.strip().title()
                    result.other_fees[key] = amount
        except Exception:
            pass

    def _extract_into(self, browser, result):
        """More thorough extraction pass after a step completes."""
        self._try_extract(browser, result)
        # Try to get product name if not set
        if not result.product_name:
            try:
                title = browser._driver.title
                if title:
                    result.product_name = title.split("|")[0].split("-")[0].strip()[:100]
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_playbook(self, domain):
        safe = domain.replace(".", "_")
        path = os.path.join(self.playbook_dir, f"{safe}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def _remap_url(self, recorded_url, recorded_base, new_base):
        """
        Remap a URL from the recorded session to the new store URL.

        For same-domain navigations (e.g. /cart, /checkout), preserve the path.
        For cross-domain or when running against a different store, use new_base.
        """
        recorded_domain = _domain_from_url(recorded_base)
        step_domain = _domain_from_url(recorded_url)

        if step_domain == recorded_domain:
            # Same domain — remap to new store's domain with same path
            parsed_new = urlparse(new_base)
            parsed_step = urlparse(recorded_url)
            # If the step URL looks like a cart/checkout path, keep it
            path = parsed_step.path
            if any(p in path for p in ("/cart", "/checkout", "/basket", "/order")):
                return f"{parsed_new.scheme}://{parsed_new.netloc}{path}"
        return recorded_url
