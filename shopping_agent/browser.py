"""Selenium browser controller for the shopping agent."""
import base64
import time

from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class Browser:
    def __init__(self, headless=True, timeout=15):
        self.timeout = timeout
        self._display = None
        self._driver = None
        self._headless = headless

    def start(self):
        if self._headless:
            self._display = Display(visible=0, size=(1280, 900))
            self._display.start()

        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        self._driver = webdriver.Chrome(options=options)
        self._driver.set_page_load_timeout(30)
        self._driver.implicitly_wait(3)
        return self

    def navigate(self, url):
        self._driver.get(url)
        time.sleep(1.5)

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
        """Click an element by CSS selector or XPath."""
        by = By.XPATH if by_xpath else By.CSS_SELECTOR
        try:
            el = WebDriverWait(self._driver, self.timeout).until(
                EC.element_to_be_clickable((by, selector))
            )
            self._driver.execute_script("arguments[0].scrollIntoView(true);", el)
            time.sleep(0.3)
            el.click()
            time.sleep(1)
            return True
        except (TimeoutException, NoSuchElementException, WebDriverException) as e:
            return f"Click failed: {e}"

    def type_text(self, selector, text, by_xpath=False, clear_first=True):
        """Type text into a form field."""
        by = By.XPATH if by_xpath else By.CSS_SELECTOR
        try:
            el = WebDriverWait(self._driver, self.timeout).until(
                EC.presence_of_element_located((by, selector))
            )
            if clear_first:
                el.clear()
            el.send_keys(text)
            time.sleep(0.5)
            return True
        except (TimeoutException, NoSuchElementException, WebDriverException) as e:
            return f"Type failed: {e}"

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
