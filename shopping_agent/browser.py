"""Selenium browser controller for the shopping agent.

Uses undetected-chromedriver + selenium-stealth to avoid bot detection
on sites like Uber Eats, DoorDash, Amazon, etc. that use Cloudflare
or fingerprint-based blocking.

Cookie persistence: cookies and localStorage are saved per-domain so the
browser looks like a returning human visitor on every run.
"""
import base64
import json
import os
import random
import tempfile
import time
import zipfile
from urllib.parse import urlparse

from pyvirtualdisplay import Display
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    import undetected_chromedriver as uc
    _HAS_UC = True
except ImportError:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    _HAS_UC = False

try:
    from selenium_stealth import stealth
    _HAS_STEALTH = True
except ImportError:
    _HAS_STEALTH = False

# JavaScript patches injected on every page to mask WebDriver fingerprints
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = {runtime: {}};
"""

# Common cookie consent button selectors (ordered most-specific first)
_CONSENT_SELECTORS = [
    # OneTrust (very common — Uber Eats, many others)
    "#onetrust-accept-btn-handler",
    # Cookiebot
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    # Cookie Consent (osano)
    ".cc-btn.cc-allow",
    # TrustArc
    ".truste_popframe .pdynamicbutton a",
    # Quantcast
    ".qc-cmp2-summary-buttons button:last-child",
    # Generic patterns
    "[data-testid='accept-cookies']",
    "[data-testid='cookie-accept']",
    "[aria-label='Accept cookies']",
    "#accept-cookies",
    "#cookie-accept",
    ".accept-cookies",
    ".cookie-accept",
    "[id*='cookie'][id*='accept']",
    "[class*='cookie'][class*='accept']",
]

# XPath fallbacks for text-based consent buttons
_CONSENT_XPATHS = [
    "//button[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='accept all']",
    "//button[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='accept all cookies']",
    "//button[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='allow all']",
    "//button[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='allow all cookies']",
    "//button[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='accept']",
    "//button[translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='i accept']",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all')]",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'got it')]",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'agree')]",
]

# Default cookie storage directory
_DEFAULT_COOKIE_DIR = os.path.join(os.path.expanduser("~"), ".shopping_agent", "cookies")


def _create_proxy_extension(proxy: dict) -> str:
    """Create a temporary Chrome extension zip that routes traffic through a proxy.

    Supports both HTTP and SOCKS5 proxies with username/password auth.

    Args:
        proxy: dict with keys: host, port, username, password, scheme ("http"|"socks5")

    Returns:
        Path to the temporary zip file (caller should delete when done).
    """
    scheme = proxy.get("scheme", "socks5")
    host = proxy["host"]
    port = int(proxy["port"])
    username = proxy["username"]
    password = proxy["password"]

    manifest = json.dumps({
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0",
    })

    background_js = f"""
var config = {{
    mode: "fixed_servers",
    rules: {{
        singleProxy: {{ scheme: "{scheme}", host: "{host}", port: {port} }},
        bypassList: ["localhost", "127.0.0.1"]
    }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
chrome.webRequest.onAuthRequired.addListener(
    function(details) {{
        return {{
            authCredentials: {{
                username: "{username}",
                password: "{password}"
            }}
        }};
    }},
    {{urls: ["<all_urls>"]}},
    ["blocking"]
);
"""

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, prefix="sa_proxy_")
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w") as zp:
        zp.writestr("manifest.json", manifest)
        zp.writestr("background.js", background_js)
    return tmp.name


def _human_delay(min_ms=80, max_ms=220):
    """Sleep for a random human-like interval."""
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _slow_type(element, text):
    """Type text one character at a time with random delays, like a human."""
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(0.04, 0.18))


def _domain_from_url(url):
    """Extract a clean domain key from a URL, e.g. 'ubereats.com'."""
    parsed = urlparse(url)
    host = parsed.netloc or parsed.path
    # strip www.
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]  # remove port


class Browser:
    def __init__(self, headless=True, timeout=20, cookie_dir=None, proxy=None):
        """
        Args:
            headless:   Run without a visible window.
            timeout:    Default WebDriver wait timeout in seconds.
            cookie_dir: Directory for persisted cookie/localStorage files.
            proxy:      Optional dict or ProxyConfig with keys:
                        host, port, username, password, scheme ("socks5"|"http").
                        When set, all browser traffic is routed through this proxy.
        """
        self.timeout = timeout
        self._display = None
        self._driver = None
        self._headless = headless
        self.cookie_dir = cookie_dir or _DEFAULT_COOKIE_DIR
        os.makedirs(self.cookie_dir, exist_ok=True)
        # Normalise proxy to a plain dict (accepts ProxyConfig dataclass too)
        if proxy is not None and hasattr(proxy, "to_dict"):
            proxy = proxy.to_dict()
        self._proxy = proxy
        self._proxy_ext_path = None  # temp file to clean up on quit

    def start(self):
        if self._headless:
            self._display = Display(visible=0, size=(1366, 768))
            self._display.start()

        if _HAS_UC:
            self._driver = self._start_undetected_chrome()
        else:
            self._driver = self._start_plain_chrome()

        # Inject stealth JS on every new document
        self._driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _STEALTH_JS},
        )

        # Apply selenium-stealth patches if available
        if _HAS_STEALTH:
            stealth(
                self._driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )

        self._driver.set_page_load_timeout(45)
        return self

    # ------------------------------------------------------------------
    # Driver factories
    # ------------------------------------------------------------------

    def _start_undetected_chrome(self):
        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--lang=en-US")
        options.add_argument("--disable-notifications")
        w = random.randint(1280, 1440)
        h = random.randint(720, 900)
        options.add_argument(f"--window-size={w},{h}")
        if self._proxy:
            self._proxy_ext_path = _create_proxy_extension(self._proxy)
            options.add_extension(self._proxy_ext_path)
            print(
                f"[Browser] Proxy: {self._proxy['scheme']}://"
                f"{self._proxy['host']}:{self._proxy['port']}"
            )
        driver = uc.Chrome(options=options, headless=self._headless, version_main=None)
        return driver

    def _start_plain_chrome(self):
        """Fallback when undetected-chromedriver is not installed."""
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--lang=en-US")
        options.add_argument("--disable-notifications")
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        if self._proxy:
            self._proxy_ext_path = _create_proxy_extension(self._proxy)
            options.add_extension(self._proxy_ext_path)
            print(
                f"[Browser] Proxy: {self._proxy['scheme']}://"
                f"{self._proxy['host']}:{self._proxy['port']}"
            )
        driver = webdriver.Chrome(options=options)
        return driver

    # ------------------------------------------------------------------
    # Cookie persistence
    # ------------------------------------------------------------------

    def _cookie_path(self, domain):
        safe = domain.replace(".", "_").replace("/", "_")
        return os.path.join(self.cookie_dir, f"{safe}.json")

    def _local_storage_path(self, domain):
        safe = domain.replace(".", "_").replace("/", "_")
        return os.path.join(self.cookie_dir, f"{safe}_localstorage.json")

    def load_cookies(self, url):
        """
        Load saved cookies and localStorage for the domain into the browser.

        Must be called AFTER navigating to the domain's root page so the
        browser is on the correct origin before adding cookies.
        Returns True if cookies were found and loaded, False otherwise.
        """
        domain = _domain_from_url(url)
        cookie_path = self._cookie_path(domain)
        ls_path = self._local_storage_path(domain)
        loaded = False

        # Load HTTP cookies
        if os.path.exists(cookie_path):
            try:
                with open(cookie_path) as f:
                    cookies = json.load(f)
                self._driver.delete_all_cookies()
                for cookie in cookies:
                    # Remove keys Selenium can't set
                    cookie.pop("expiry", None)
                    cookie.pop("sameSite", None)
                    try:
                        self._driver.add_cookie(cookie)
                    except Exception:
                        continue
                loaded = True
                print(f"[Browser] Loaded {len(cookies)} cookies for {domain}")
            except Exception as e:
                print(f"[Browser] Could not load cookies for {domain}: {e}")

        # Restore localStorage
        if os.path.exists(ls_path):
            try:
                with open(ls_path) as f:
                    ls_data = json.load(f)
                for key, value in ls_data.items():
                    self._driver.execute_script(
                        "window.localStorage.setItem(arguments[0], arguments[1]);",
                        key, value,
                    )
                print(f"[Browser] Restored {len(ls_data)} localStorage keys for {domain}")
            except Exception as e:
                print(f"[Browser] Could not restore localStorage for {domain}: {e}")

        return loaded

    def save_cookies(self, url):
        """
        Persist the current browser's cookies and localStorage for the domain.
        Call this after a successful session to build up a real-user cookie profile.
        """
        domain = _domain_from_url(url)
        cookie_path = self._cookie_path(domain)
        ls_path = self._local_storage_path(domain)

        # Save HTTP cookies
        try:
            cookies = self._driver.get_cookies()
            with open(cookie_path, "w") as f:
                json.dump(cookies, f, indent=2)
            print(f"[Browser] Saved {len(cookies)} cookies for {domain}")
        except Exception as e:
            print(f"[Browser] Could not save cookies for {domain}: {e}")

        # Save localStorage
        try:
            ls_data = self._driver.execute_script(
                "var items = {}; "
                "for (var i = 0; i < window.localStorage.length; i++) {"
                "  var k = window.localStorage.key(i);"
                "  items[k] = window.localStorage.getItem(k);"
                "} return items;"
            )
            if ls_data:
                with open(ls_path, "w") as f:
                    json.dump(ls_data, f, indent=2)
                print(f"[Browser] Saved {len(ls_data)} localStorage keys for {domain}")
        except Exception as e:
            print(f"[Browser] Could not save localStorage for {domain}: {e}")

    def accept_cookie_consent(self):
        """
        Try to click cookie/GDPR consent banners automatically.
        Tries a battery of known selectors and XPath patterns.
        Returns True if a banner was dismissed, False if none found.
        """
        _human_delay(600, 1200)

        # Try CSS selectors first
        for selector in _CONSENT_SELECTORS:
            try:
                el = WebDriverWait(self._driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                ActionChains(self._driver).move_to_element(el).perform()
                _human_delay(200, 500)
                el.click()
                _human_delay(500, 1000)
                print(f"[Browser] Accepted cookie consent via: {selector}")
                return True
            except (TimeoutException, NoSuchElementException, WebDriverException):
                continue

        # Try XPath text-based patterns
        for xpath in _CONSENT_XPATHS:
            try:
                el = WebDriverWait(self._driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                ActionChains(self._driver).move_to_element(el).perform()
                _human_delay(200, 500)
                el.click()
                _human_delay(500, 1000)
                print(f"[Browser] Accepted cookie consent via XPath")
                return True
            except (TimeoutException, NoSuchElementException, WebDriverException):
                continue

        return False

    def has_cookies_for(self, url):
        """Return True if a saved cookie file exists for this domain."""
        domain = _domain_from_url(url)
        return os.path.exists(self._cookie_path(domain))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, url):
        self._driver.get(url)
        self._wait_for_page_load()
        _human_delay(800, 1800)

    def navigate_with_cookies(self, url):
        """
        Navigate to a URL with full cookie restoration:
        1. Go to the domain root to establish origin
        2. Inject saved cookies + localStorage
        3. Reload to the actual URL as a "returning user"
        Returns True if existing cookies were loaded, False if fresh session.
        """
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        # Step 1: land on the domain (needed before we can set cookies)
        self._driver.get(origin)
        self._wait_for_page_load()
        _human_delay(400, 800)

        # Step 2: inject saved cookies
        had_cookies = self.load_cookies(url)

        # Step 3: navigate to actual URL — browser now looks like a returning visitor
        self._driver.get(url)
        self._wait_for_page_load()
        _human_delay(800, 1800)

        # Step 4: accept any consent banner (builds up more cookies)
        self.accept_cookie_consent()

        return had_cookies

    def _wait_for_page_load(self, timeout=30):
        try:
            WebDriverWait(self._driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            pass

    def get_page_source(self):
        return self._driver.page_source

    def get_current_url(self):
        return self._driver.current_url

    def get_page_text(self):
        try:
            body = self._driver.find_element(By.TAG_NAME, "body")
            return body.text
        except Exception:
            return ""

    def is_blocked(self):
        """Detect common bot-block / CAPTCHA pages."""
        title = self._driver.title.lower()
        source_snippet = self._driver.page_source[:3000].lower()
        block_signals = [
            "just a moment",       # Cloudflare
            "access denied",
            "captcha",
            "are you a robot",
            "verify you are human",
            "security check",
            "ddos-guard",
            "enable javascript",
            "checking your browser",
        ]
        return any(s in title or s in source_snippet for s in block_signals)

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def find_elements(self, selector, by_xpath=False):
        """Return list of dicts describing matched elements."""
        by = By.XPATH if by_xpath else By.CSS_SELECTOR
        try:
            elements = self._driver.find_elements(by, selector)
        except WebDriverException:
            return []

        results = []
        for el in elements[:20]:
            try:
                results.append({
                    "text": el.text[:200],
                    "href": el.get_attribute("href") or "",
                    "id": el.get_attribute("id") or "",
                    "class": el.get_attribute("class") or "",
                    "value": el.get_attribute("value") or "",
                    "tag": el.tag_name,
                })
            except Exception:
                continue
        return results

    def click(self, selector, by_xpath=False):
        """Click an element using human-like mouse movement."""
        by = By.XPATH if by_xpath else By.CSS_SELECTOR
        try:
            el = WebDriverWait(self._driver, self.timeout).until(
                EC.element_to_be_clickable((by, selector))
            )
            self._driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            _human_delay(300, 700)
            ActionChains(self._driver).move_to_element(el).perform()
            _human_delay(100, 300)
            el.click()
            _human_delay(600, 1400)
            return True
        except (TimeoutException, NoSuchElementException, WebDriverException) as e:
            return f"Click failed: {e}"

    def type_text(self, selector, text, by_xpath=False, clear_first=True):
        """Type text into a field with human-like keystroke timing."""
        by = By.XPATH if by_xpath else By.CSS_SELECTOR
        try:
            el = WebDriverWait(self._driver, self.timeout).until(
                EC.presence_of_element_located((by, selector))
            )
            ActionChains(self._driver).move_to_element(el).perform()
            _human_delay(100, 300)
            el.click()
            if clear_first:
                el.clear()
                _human_delay(80, 150)
            _slow_type(el, text)
            _human_delay(200, 500)
            return True
        except (TimeoutException, NoSuchElementException, WebDriverException) as e:
            return f"Type failed: {e}"

    def scroll(self, direction="bottom", selector=None):
        """Scroll the page. direction: 'bottom', 'top', or 'element'."""
        if direction == "bottom":
            self._driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        elif direction == "top":
            self._driver.execute_script("window.scrollTo(0, 0);")
        elif direction == "element" and selector:
            try:
                el = self._driver.find_element(By.CSS_SELECTOR, selector)
                self._driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            except Exception:
                pass
        _human_delay(400, 800)

    def screenshot(self):
        """Return a base64-encoded PNG screenshot."""
        try:
            png = self._driver.get_screenshot_as_png()
            return base64.b64encode(png).decode("utf-8")
        except Exception as e:
            return f"Screenshot failed: {e}"

    def quit(self):
        try:
            if self._driver:
                self._driver.quit()
        except Exception:
            pass
        try:
            if self._display:
                self._display.stop()
        except Exception:
            pass
        # Clean up the temporary proxy extension zip
        if self._proxy_ext_path and os.path.exists(self._proxy_ext_path):
            try:
                os.unlink(self._proxy_ext_path)
            except Exception:
                pass
