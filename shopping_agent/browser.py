"""Selenium browser controller for the shopping agent.

Uses undetected-chromedriver + selenium-stealth to avoid bot detection
on sites like Uber Eats, DoorDash, Amazon, etc. that use Cloudflare
or fingerprint-based blocking.
"""
import base64
import random
import time

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


def _human_delay(min_ms=80, max_ms=220):
    """Sleep for a random human-like interval."""
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _slow_type(element, text):
    """Type text one character at a time with random delays, like a human."""
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(0.04, 0.18))


class Browser:
    def __init__(self, headless=True, timeout=20):
        self.timeout = timeout
        self._display = None
        self._driver = None
        self._headless = headless

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
        options.add_argument("--window-size=1366,768")
        options.add_argument("--lang=en-US")
        options.add_argument("--disable-notifications")
        # Randomise window size slightly so every session looks different
        w = random.randint(1280, 1440)
        h = random.randint(720, 900)
        options.add_argument(f"--window-size={w},{h}")
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
        driver = webdriver.Chrome(options=options)
        return driver

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(self, url):
        self._driver.get(url)
        # Wait for page load + extra random delay to mimic human reading time
        self._wait_for_page_load()
        _human_delay(800, 1800)

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
            # Move mouse to element before clicking (avoids "robot teleport" signals)
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
