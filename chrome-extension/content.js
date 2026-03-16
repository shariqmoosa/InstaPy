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
  if (msg.type === "SHOW_TOAST")           { showToast(msg.text, msg.style); }
  if (msg.type === "SHOW_COMBO_BANNER")    { showComboBanner(msg); }
  if (msg.type === "BUILD_CART_TO_TARGET") { buildCartToTarget(msg); }
  if (msg.type === "CLICK_CHECKOUT") {
    const btn = _findCheckoutButton();
    if (btn) { btn.click(); sendResponse({ ok: true }); }
    else sendResponse({ ok: false });
    return true;
  }
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

// Keywords that label a subtotal/item-total row
const _SUBTOTAL_LABELS = /\b(?:subtotal|item\s+total|items?\s+subtotal|items?\s+total|est(?:imated)?\s+subtotal|merch(?:andise)?\s+total|order\s+items?)\b/i;
// Dollar amount pattern
const _PRICE_RE = /^\$?\s*([\d,]+\.\d{2})$/;

function _parseDollar(text) {
  const m = text.trim().match(_PRICE_RE);
  if (!m) return null;
  const v = parseFloat(m[1].replace(/,/g, ""));
  return (v > 0 && v < 500) ? v : null;
}

// Walk up to `depth` ancestors and their siblings looking for a label keyword.
function _nearbyHasLabel(el, depth) {
  let cur = el;
  for (let i = 0; i <= depth; i++) {
    if (!cur) break;
    // Check siblings at this level
    let sib = cur.previousElementSibling;
    while (sib) {
      if (_SUBTOTAL_LABELS.test(sib.textContent)) return true;
      sib = sib.previousElementSibling;
    }
    sib = cur.nextElementSibling;
    while (sib) {
      if (_SUBTOTAL_LABELS.test(sib.textContent)) return true;
      sib = sib.nextElementSibling;
    }
    // Check parent's own text (excluding children)
    if (cur.parentElement && _SUBTOTAL_LABELS.test(cur.parentElement.textContent)) return true;
    cur = cur.parentElement;
  }
  return false;
}

function detectCheckoutSubtotal() {
  // Strategy 1: DOM proximity — find a price element whose nearby DOM says "subtotal"
  const allEls = Array.from(document.querySelectorAll("span, div, p, td, strong, b"));
  for (const el of allEls) {
    const children = el.children.length;
    if (children > 3) continue; // skip containers with lots of children
    const v = _parseDollar(el.textContent);
    if (v === null) continue;
    if (_nearbyHasLabel(el, 3)) return v;
  }

  // Strategy 2: flat-text regex fallback
  const text = extractText();
  const patterns = [
    /(?:subtotal|item\s+total|items?\s+subtotal|items?\s+total|est\.?\s*subtotal|merch(?:andise)?\s+total)[\s\S]{0,30}?\$\s*([\d,]+\.\d{2})/i,
    /\$\s*([\d,]+\.\d{2})\s*\n?\s*(?:subtotal|item\s+total)/i,
    /\bitems?\s*(?:\(\d+\))?\s*\$\s*([\d,]+\.\d{2})/i,
  ];
  for (const p of patterns) {
    const m = text.match(p);
    if (m) {
      const v = parseFloat(m[1].replace(/,/g, ""));
      if (v > 0 && v < 500) return v;
    }
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
      pageText: extractText(),
      subtotal: detectCheckoutSubtotal(), // regex fallback so AI doesn't need to find it
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

// ── Cart automation ───────────────────────────────────────────────────────────
// After the user manually builds the first cart, the extension automatically
// builds the remaining basket sizes by:
//   1. Going back to the store page  (history.back)
//   2. Clicking the cheapest item's + button until subtotal hits the target range
//   3. Clicking the checkout button
// notifyCheckout() then fires naturally, triggering auto-capture in background.

const _BASKET_LO = { 10: 1,    25: 17.5, 50: 37.5, 75: 62.5, 100: 87.5 };
const _BASKET_HI = { 10: 17.49, 25: 37.49, 50: 62.49, 75: 87.49, 100: 999 };

const _sleep = ms => new Promise(r => setTimeout(r, ms));

async function buildCartToTarget({ target, currentSubtotal, platform }) {
  showToast(`🛒 Building $${target} basket…`, "ok");

  // Go back to store (cart stays intact on SPAs)
  history.back();
  await _sleep(5000); // wait longer for DoorDash SPA to fully load the cart

  const lo = _BASKET_LO[target] ?? target * 0.7;
  const hi = _BASKET_HI[target] ?? target * 1.5;

  let iters = 0;
  while (iters < 60) {
    const sub = _readCartSubtotal() ?? currentSubtotal;

    if (sub >= lo && sub <= hi) break;  // in range — done

    if (sub > hi) {
      showToast(`⚠️ Cart $${sub.toFixed(2)} overshot $${target} range — adjust manually then go to checkout`, "err");
      return;
    }

    const btn = _findPlusButton();
    if (!btn) {
      showToast(`⚠️ Can't find a cart + button — add items to ~$${target} then go to checkout`, "err");
      return;
    }

    btn.click();
    await _sleep(1200);
    iters++;
  }

  if (iters >= 60) {
    showToast(`⚠️ Couldn't reach $${target} in 60 clicks — add remaining items manually`, "err");
    return;
  }

  // Click checkout — notifyCheckout() will fire and auto-capture the result
  await _sleep(500);
  const checkoutBtn = _findCheckoutButton();
  if (checkoutBtn) {
    checkoutBtn.click();
  } else {
    showToast(`🛒 Cart ready at ~$${target} — click "Go to Checkout" to continue`, "ok");
  }
}

function _readCartSubtotal() {
  // Reuse the same DOM-proximity logic as detectCheckoutSubtotal
  return detectCheckoutSubtotal();
}

function _findPlusButton() {
  // Platform-specific test-ids first, then generic aria-label, then "+" text fallback.
  // We take the LAST visible match — in most cart layouts the last + button belongs to
  // the cheapest item (items are listed top=expensive to bottom=cheap, or we just pick
  // any item since basket ranges are wide enough to tolerate any item price <$20).
  const selectors = [
    '[data-testid="Cart-Item-incrementButton"]',   // DoorDash
    '[data-testid="increment-btn"]',               // DoorDash alt
    '[data-testid="cart_item_increment"]',         // Instacart
    'button[aria-label*="Increase quantity"]',     // Uber Eats / generic
    'button[aria-label*="Add one more"]',          // Instacart
    'button[aria-label*="Increase"]',              // generic
    '[data-testid*="increment"]',                  // generic test-id
    '[data-testid*="plus"]',                       // generic test-id
  ];
  for (const sel of selectors) {
    const visible = Array.from(document.querySelectorAll(sel)).filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    if (visible.length) return visible[visible.length - 1];
  }
  // Last-resort: any visible button whose only text is "+"
  return Array.from(document.querySelectorAll('button, [role="button"]')).find(el => {
    const t = (el.textContent || "").trim();
    const r = el.getBoundingClientRect();
    return (t === "+" || t === "＋") && r.width > 0 && r.height > 0;
  }) || null;
}

function _findCheckoutButton() {
  const selectors = [
    '[data-testid="CartFooter-StartOrderButton"]', // DoorDash
    '[data-testid="checkout-button"]',             // DoorDash alt
    '[data-testid="go_to_checkout"]',              // Instacart
    '[data-testid="cart_checkout_button"]',        // Uber Eats
    'a[href*="/checkout"]',                        // generic link
  ];
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) { const r = el.getBoundingClientRect(); if (r.width > 0) return el; }
  }
  // Semantic fallback: button/link containing "checkout" text
  return Array.from(document.querySelectorAll('button, a, [role="button"]')).find(el => {
    const t = (el.textContent || "").trim().toLowerCase();
    const r = el.getBoundingClientRect();
    return (t.includes("checkout") || t === "place order") && r.width > 0;
  }) || null;
}

// ── Combo progress banner ─────────────────────────────────────────────────────
// Persistent pill at bottom of page showing which basket sizes are done.
// Appears after first capture, updates after each subsequent capture on same tab.

const _ALL_BASKETS  = [10, 25, 50, 75, 100];
const _PLAT_ABBREV  = { "DoorDash": "DD", "Instacart": "IC", "Uber Eats": "UE" };
let   _comboBanner  = null;

function showComboBanner({ platform, retailerName, membership, capturedBaskets, remainingBaskets }) {
  if (_comboBanner) _comboBanner.remove();

  // All done for this membership — congratulate and dismiss
  if (remainingBaskets.length === 0) {
    showToast(`🎉 All ${membership} baskets done for ${retailerName}!`, "ok");
    return;
  }

  const plat  = _PLAT_ABBREV[platform] || platform;
  const chips = _ALL_BASKETS.map(b => {
    if (capturedBaskets.includes(b))
      return `<span style="color:#4caf82;font-weight:600">$${b}&nbsp;✓</span>`;
    if (b === remainingBaskets[0])
      return `<span style="background:#221a3a;color:#a090f8;font-weight:700;padding:2px 7px;border-radius:5px">$${b}&nbsp;▶</span>`;
    return `<span style="color:#3a3a50">$${b}</span>`;
  }).join(`<span style="color:#222;padding:0 3px">·</span>`);

  _comboBanner = document.createElement("div");
  _comboBanner.id = "_fc_combo";
  _comboBanner.innerHTML = `
    <span style="color:#555;font-size:11px;margin-right:10px;white-space:nowrap">${plat}&nbsp;·&nbsp;${retailerName}&nbsp;·&nbsp;${membership}</span>
    <span style="display:flex;align-items:center;gap:4px">${chips}</span>
    <button id="_fc_combo_x" style="background:none;border:none;color:#333;cursor:pointer;margin-left:12px;font-size:14px;line-height:1;padding:0">✕</button>`;
  Object.assign(_comboBanner.style, {
    position:   "fixed",
    bottom:     "20px",
    left:       "50%",
    transform:  "translateX(-50%)",
    background: "#0d0d18",
    border:     "1px solid #1c1c2c",
    color:      "#fff",
    padding:    "9px 16px",
    borderRadius: "24px",
    zIndex:     "2147483647",
    fontFamily: "monospace",
    fontSize:   "13px",
    display:    "flex",
    alignItems: "center",
    gap:        "4px",
    boxShadow:  "0 6px 28px rgba(0,0,0,0.65)",
    whiteSpace: "nowrap",
  });
  document.body.appendChild(_comboBanner);
  document.getElementById("_fc_combo_x").addEventListener("click", () => {
    _comboBanner?.remove();
    _comboBanner = null;
  });
}

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
