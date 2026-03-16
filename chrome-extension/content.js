"use strict";

// ── Page data extraction ───────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GET_PAGE_DATA") {
    sendResponse({ url: location.href, title: document.title, pageText: extractText() });
    return true;
  }
  if (msg.type === "RECORDING_START")  { startRecording(); }
  if (msg.type === "RECORDING_STOP")   { stopRecording(); }
  if (msg.type === "UPDATE_COUNT")     { updateOverlayCount(msg.count); }
  if (msg.type === "RECORDING_SAVED")  { showSavedState(msg.store, msg.stepCount); }
  if (msg.type === "REPLAY_DONE")      { showToast("✅ Replay done — analyzing fees…"); }
  if (msg.type === "SHOW_TOAST")       { showToast(msg.text, msg.style); }
});

// ── Text extraction ───────────────────────────────────────────────────────────

function extractText() {
  const parts = [];
  const seen = new Set();
  function walk(node) {
    if (!node) return;
    if (node.nodeType === Node.TEXT_NODE) {
      const t = node.textContent.trim();
      if (t && !seen.has(t)) { seen.add(t); parts.push(t); }
      return;
    }
    const tag = node.tagName?.toLowerCase();
    if (tag === "script" || tag === "style" || tag === "noscript") return;
    for (const child of node.childNodes) walk(child);
  }
  walk(document.body);
  return parts.join("\n").slice(0, 12000);
}

// ── Checkout detection ────────────────────────────────────────────────────────
// When the user lands on a checkout page for any of the three target platforms,
// we wait 3s for fees to render then send the full page text to background.
// Background auto-captures without any popup interaction needed.

const CHECKOUT_PATTERNS = [
  { platform: "DoorDash",  hostRe: /doordash\.com$/,  pathRe: /\/checkout|\/confirm-order/ },
  { platform: "Instacart", hostRe: /instacart\.com$/, pathRe: /\/checkout|\/place_order/   },
  { platform: "Uber Eats", hostRe: /ubereats\.com$/,  pathRe: /\/checkout/                 },
];

function detectCheckoutPlatform() {
  const host = location.hostname.replace(/^www\./, "");
  const path = location.pathname;
  for (const p of CHECKOUT_PATTERNS) {
    if (p.hostRe.test(host) && p.pathRe.test(path)) return p.platform;
  }
  return null;
}

function detectRetailerFromTitle() {
  const m = document.title.match(/^(.+?)\s*[-–—|]\s*(DoorDash|Instacart|Uber Eats)/i);
  return m ? m[1].trim() : null;
}

let _checkoutTimer = null;

function notifyCheckout() {
  const platform = detectCheckoutPlatform();
  if (!platform) return;

  // Debounce: if URL changed quickly (SPA nav) reset the timer
  if (_checkoutTimer) clearTimeout(_checkoutTimer);

  // Wait 3s for the checkout page to fully render all fees before grabbing text
  _checkoutTimer = setTimeout(() => {
    _checkoutTimer = null;
    chrome.runtime.sendMessage({
      type: "CHECKOUT_DETECTED",
      platform,
      retailer: detectRetailerFromTitle() || "",
      url: location.href,
      pageText: extractText(),  // include full page text for auto-capture
    }).catch(() => {});
  }, 3000);
}

notifyCheckout(); // run on initial load

// ── Recording ─────────────────────────────────────────────────────────────────

let _recording = false;
let _lastUrl = location.href;
let _overlay = null;
let _stepCount = 0;

function getBestSelector(el) {
  if (!el || el === document.body) return null;
  if (el.id) return `#${CSS.escape(el.id)}`;
  const testid = el.getAttribute("data-testid");
  if (testid) return `[data-testid="${testid}"]`;
  const aria = el.getAttribute("aria-label");
  if (aria) return `[aria-label="${aria}"]`;
  const parts = [];
  let cur = el;
  for (let i = 0; i < 3 && cur && cur !== document.body; i++) {
    let seg = cur.tagName.toLowerCase();
    if (cur.className) {
      const cls = [...cur.classList].slice(0, 2).join(".");
      if (cls) seg += "." + cls;
    }
    parts.unshift(seg);
    cur = cur.parentElement;
  }
  return parts.join(" > ");
}

document.addEventListener("click", (e) => {
  if (!_recording) return;
  if (e.target.closest("#_fc_overlay")) return;
  const step = {
    type: "click",
    url: location.href,
    selector: getBestSelector(e.target),
    text: (e.target.innerText || e.target.textContent || "").trim().slice(0, 80),
  };
  chrome.runtime.sendMessage({ type: "RECORD_STEP", step });
  _stepCount++;
  updateOverlayCount(_stepCount);
}, true);

// Detect URL changes (SPA navigation)
const _navObserver = new MutationObserver(() => {
  if (location.href === _lastUrl) return;
  _lastUrl = location.href;

  if (_recording) {
    chrome.runtime.sendMessage({ type: "RECORD_STEP", step: { type: "navigate", url: location.href } });
    _stepCount++;
    updateOverlayCount(_stepCount);
  }

  // Re-check checkout on every SPA navigation
  notifyCheckout();
});
_navObserver.observe(document.documentElement, { subtree: true, childList: true });

function startRecording() {
  _recording = true;
  _stepCount = 0;
  _lastUrl = location.href;
  injectOverlay();
  chrome.runtime.sendMessage({ type: "RECORD_STEP", step: { type: "navigate", url: location.href } });
}

function stopRecording() {
  _recording = false;
  removeOverlay();
}

// ── Overlay banner ────────────────────────────────────────────────────────────

function injectOverlay() {
  if (_overlay) return;
  _overlay = document.createElement("div");
  _overlay.id = "_fc_overlay";
  _overlay.innerHTML = `
    <span style="margin-right:10px">🔴 Recording <b id="_fc_count">0</b> steps</span>
    <button id="_fc_stop" style="
      background:#e53935;color:#fff;border:none;padding:5px 14px;
      border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;">
      Stop &amp; Save
    </button>`;
  Object.assign(_overlay.style, {
    position: "fixed", top: "0", left: "50%", transform: "translateX(-50%)",
    zIndex: "2147483647", background: "#1a1a2e", color: "#fff",
    padding: "10px 20px", borderRadius: "0 0 12px 12px",
    fontFamily: "sans-serif", fontSize: "14px",
    display: "flex", alignItems: "center", gap: "8px",
    boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
  });
  document.body.appendChild(_overlay);
  document.getElementById("_fc_stop").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "STOP_RECORDING" });
    _recording = false;
  });
}

function showSavedState(store, stepCount) {
  if (!_overlay) return;
  _overlay.innerHTML = `
    <span style="margin-right:10px">
      ✅ <b>${stepCount} steps</b> saved for <b>${store}</b>
      &nbsp;·&nbsp;
      <span style="color:#aaa">Click the extension icon → Recordings → ▶ Run to auto-analyze next time</span>
    </span>
    <button id="_fc_dismiss" style="
      background:#333;color:#ccc;border:none;padding:5px 12px;
      border-radius:6px;cursor:pointer;font-size:13px;">
      Dismiss
    </button>`;
  document.getElementById("_fc_dismiss").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "DISMISS_OVERLAY" });
    removeOverlay();
  });
}

function removeOverlay() {
  if (_overlay) { _overlay.remove(); _overlay = null; }
}

function updateOverlayCount(n) {
  const el = document.getElementById("_fc_count");
  if (el) el.textContent = n;
}

// ── Init: restore overlay after full-page navigation ─────────────────────────

function _restoreRecordingState(attempt) {
  attempt = attempt || 0;
  chrome.runtime.sendMessage({ type: "RECORDING_STATE" }, (state) => {
    if (chrome.runtime.lastError) {
      if (attempt < 3) setTimeout(() => _restoreRecordingState(attempt + 1), 300 * (attempt + 1));
      return;
    }
    if (state?.recording) {
      _recording = true;
      _lastUrl = location.href;
      _stepCount = state.steps?.length || 0;
      injectOverlay();
      updateOverlayCount(_stepCount);
      chrome.runtime.sendMessage({ type: "RECORD_STEP", step: { type: "navigate", url: location.href } });
    }
  });
}

_restoreRecordingState();

// ── Toast ─────────────────────────────────────────────────────────────────────

function showToast(msg, style) {
  // style: "ok" (green), "err" (red), default (dark)
  const colors = { ok: "#1a2e22", err: "#2a1a1a" };
  const borders = { ok: "#4caf82", err: "#e05353" };
  const t = document.createElement("div");
  t.textContent = msg;
  Object.assign(t.style, {
    position: "fixed", bottom: "24px", left: "50%", transform: "translateX(-50%)",
    background: colors[style] || "#1a1a2e",
    border: `1px solid ${borders[style] || "#2a2a40"}`,
    color: "#fff", padding: "10px 20px",
    borderRadius: "10px", zIndex: "2147483647", fontFamily: "sans-serif",
    fontSize: "14px", boxShadow: "0 4px 20px rgba(0,0,0,0.4)",
    whiteSpace: "nowrap",
  });
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
