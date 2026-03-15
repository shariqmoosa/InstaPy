"""
Site recorder — opens a browser visibly, injects a JS listener that captures
every click (coordinates + CSS selector + element info) and every form input,
then saves the session as a replayable playbook JSON.

Usage:
    from shopping_agent.recorder import SiteRecorder

    with SiteRecorder() as rec:
        steps = rec.record("https://www.ubereats.com/store/mcdonalds/abc123")
        # Browser opens. User clicks around. Press ENTER in terminal to stop.

    # Later, replay it:
    from shopping_agent.playbook import PlaybookRunner
    runner = PlaybookRunner()
    runner.run("https://www.ubereats.com/store/kfc/xyz456", playbook="ubereats.com")
"""
import json
import os
import time
from datetime import datetime
from urllib.parse import urlparse

from .browser import Browser, _domain_from_url

# Default directory for saved playbooks
_DEFAULT_PLAYBOOK_DIR = os.path.join(os.path.expanduser("~"), ".shopping_agent", "playbooks")

# JS injected into the page to intercept all user events.
# Stores them in window.__sa_events[] so Python can poll them via execute_script.
_RECORDER_JS = """
(function() {
  if (window.__sa_recorder_active) return;
  window.__sa_recorder_active = true;
  window.__sa_events = [];

  // Build a short but unique CSS selector path for any element
  function cssPath(el) {
    if (!el || el === document.body) return 'body';
    var path = [];
    while (el && el.nodeType === 1 && el !== document.body) {
      var selector = el.nodeName.toLowerCase();
      if (el.id) {
        selector += '#' + el.id;
        path.unshift(selector);
        break;
      }
      // data-testid is very stable on React apps (Uber Eats, DoorDash etc.)
      var testId = el.getAttribute('data-testid');
      if (testId) {
        selector += '[data-testid="' + testId + '"]';
        path.unshift(selector);
        break;
      }
      // aria-label is stable too
      var aria = el.getAttribute('aria-label');
      if (aria) {
        selector += '[aria-label="' + aria.replace(/"/g,'\\\\\"') + '"]';
        path.unshift(selector);
        break;
      }
      // nth-child fallback
      var sib = el, nth = 1;
      while ((sib = sib.previousElementSibling)) {
        if (sib.nodeName === el.nodeName) nth++;
      }
      if (nth > 1) selector += ':nth-of-type(' + nth + ')';
      path.unshift(selector);
      el = el.parentNode;
    }
    return path.join(' > ');
  }

  function xpathOf(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '//*[@id="' + el.id + '"]';
    var text = (el.innerText || '').trim().slice(0, 60);
    if (text) return '//' + el.tagName.toLowerCase() + '[contains(text(),"' + text.replace(/"/g,'\\\\"') + '")]';
    return '';
  }

  // Click recorder
  document.addEventListener('click', function(e) {
    var el = e.target;
    window.__sa_events.push({
      type: 'click',
      x: Math.round(e.clientX),
      y: Math.round(e.clientY),
      selector: cssPath(el),
      xpath: xpathOf(el),
      text: (el.innerText || el.value || '').trim().slice(0, 100),
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      url: window.location.href,
      ts: Date.now()
    });
  }, true);

  // Input recorder (fires on blur so we capture the final value)
  document.addEventListener('change', function(e) {
    var el = e.target;
    if (!['input','select','textarea'].includes(el.tagName.toLowerCase())) return;
    window.__sa_events.push({
      type: 'input',
      selector: cssPath(el),
      xpath: xpathOf(el),
      value: el.value,
      input_type: el.type || '',
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      url: window.location.href,
      ts: Date.now()
    });
  }, true);

  console.log('[ShoppingAgent] Recorder active');
})();
"""


class SiteRecorder:
    """
    Records user interactions with a website and saves them as a playbook.

    Opens a VISIBLE (non-headless) browser so you can click around normally.
    Every click and form input is captured with CSS selector, XPath, coordinates,
    and element text — giving the replayer multiple fallback strategies.
    """

    def __init__(self, playbook_dir=None, cookie_dir=None):
        self.playbook_dir = playbook_dir or _DEFAULT_PLAYBOOK_DIR
        self.cookie_dir = cookie_dir
        os.makedirs(self.playbook_dir, exist_ok=True)
        self._browser = None
        self._steps = []
        self._last_url = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()

    def record(self, url, description=""):
        """
        Open the browser at url and record all user interactions until
        the user presses ENTER in the terminal.

        Returns list of recorded steps (also saved to playbook file).
        """
        print("\n" + "="*60)
        print("RECORDING MODE")
        print("="*60)
        print(f"Opening: {url}")
        print("Interact with the site normally in the browser window.")
        print("Press ENTER here when done to stop recording.")
        print("="*60 + "\n")

        # Always non-headless for recording
        self._browser = Browser(headless=False, cookie_dir=self.cookie_dir)
        self._browser.start()
        self._steps = []
        self._last_url = None

        # Load cookies so the recording session is realistic
        self._browser.navigate_with_cookies(url)
        self._last_url = url

        # Inject recorder JS
        self._inject_recorder()

        # Record a navigate step
        self._steps.append({
            "type": "navigate",
            "url": url,
            "label": "Initial navigation",
        })

        # Poll for events until user presses ENTER
        try:
            import threading
            stop_event = threading.Event()

            def wait_for_enter():
                input()
                stop_event.set()

            t = threading.Thread(target=wait_for_enter, daemon=True)
            t.start()

            while not stop_event.is_set():
                self._poll_events()
                self._check_navigation()
                time.sleep(0.3)

            # Final poll to catch last events
            self._poll_events()

        except KeyboardInterrupt:
            pass

        playbook = self._save(url, description)
        print(f"\n[Recorder] Saved {len(self._steps)} steps → {playbook}")
        return self._steps

    def stop(self):
        if self._browser:
            try:
                self._browser.quit()
            except Exception:
                pass
            self._browser = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _inject_recorder(self):
        try:
            self._browser._driver.execute_script(_RECORDER_JS)
        except Exception as e:
            print(f"[Recorder] JS inject warning: {e}")

    def _poll_events(self):
        """Drain window.__sa_events[] and convert to steps."""
        try:
            events = self._browser._driver.execute_script(
                "return (window.__sa_events || []).splice(0);"
            )
        except Exception:
            return

        for ev in events:
            ev_type = ev.get("type")
            if ev_type == "click":
                # Skip clicks with no useful selector
                sel = ev.get("selector", "")
                if not sel or sel == "body":
                    continue
                self._steps.append({
                    "type": "click",
                    "selector": sel,
                    "xpath": ev.get("xpath", ""),
                    "coords": {"x": ev.get("x", 0), "y": ev.get("y", 0)},
                    "text": ev.get("text", ""),
                    "tag": ev.get("tag", ""),
                    "url": ev.get("url", ""),
                    "label": f"Click: {ev.get('text', sel)[:60]}",
                })
            elif ev_type == "input":
                self._steps.append({
                    "type": "input",
                    "selector": ev.get("selector", ""),
                    "xpath": ev.get("xpath", ""),
                    "value": ev.get("value", ""),
                    "input_type": ev.get("input_type", ""),
                    "url": ev.get("url", ""),
                    "label": f"Type: {ev.get('value', '')[:40]}",
                })

    def _check_navigation(self):
        """Detect page navigations and record them as steps."""
        try:
            current = self._browser._driver.current_url
        except Exception:
            return

        if current != self._last_url and current not in ("about:blank", "data:,"):
            # Re-inject recorder JS after navigation (SPA or full page load)
            self._inject_recorder()
            if self._last_url is not None:
                self._steps.append({
                    "type": "navigate",
                    "url": current,
                    "label": f"Navigation to {current[:80]}",
                })
            self._last_url = current

    def _save(self, url, description):
        domain = _domain_from_url(url)
        playbook = {
            "store": domain,
            "description": description or f"Recorded session for {domain}",
            "recorded_at": datetime.utcnow().isoformat() + "Z",
            "base_url": url,
            "steps": self._steps,
        }
        path = os.path.join(self.playbook_dir, f"{domain.replace('.', '_')}.json")
        with open(path, "w") as f:
            json.dump(playbook, f, indent=2)
        return path

    def list_playbooks(self):
        """Return list of (domain, path) for all saved playbooks."""
        result = []
        for fname in os.listdir(self.playbook_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.playbook_dir, fname)
                try:
                    with open(path) as f:
                        data = json.load(f)
                    result.append((data.get("store", fname), path))
                except Exception:
                    pass
        return result
