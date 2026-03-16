"use strict";

// All recording state is written to chrome.storage.session so it survives
// service worker restarts (MV3 workers are killed after ~30s of inactivity).

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

  // ── Recording ──────────────────────────────────────────────────────────────

  if (msg.type === "RECORDING_STATE") {
    chrome.storage.session.get({ rec: null }, ({ rec }) => sendResponse(rec || {}));
    return true;
  }

  if (msg.type === "START_RECORDING") {
    const rec = { recording: true, steps: [], store: msg.store, tabId: msg.tabId };
    chrome.storage.session.set({ rec }, () => {
      chrome.tabs.sendMessage(msg.tabId, { type: "RECORDING_START" });
      sendResponse({ ok: true });
    });
    return true;
  }

  if (msg.type === "RECORD_STEP") {
    chrome.storage.session.get({ rec: null }, ({ rec }) => {
      if (!rec?.recording) return;
      rec.steps.push(msg.step);
      chrome.storage.session.set({ rec });
      chrome.tabs.sendMessage(rec.tabId, { type: "UPDATE_COUNT", count: rec.steps.length }).catch(() => {});
    });
  }

  if (msg.type === "STOP_RECORDING") {
    chrome.storage.session.get({ rec: null }, ({ rec }) => {
      if (!rec) { sendResponse({ steps: [] }); return; }
      rec.recording = false;
      chrome.storage.session.set({ rec });
      const { store, steps, tabId } = rec;
      if (steps.length > 0) {
        chrome.storage.local.get({ recordings: {} }, ({ recordings }) => {
          recordings[store] = { steps, savedAt: new Date().toISOString() };
          chrome.storage.local.set({ recordings });
        });
      }
      if (tabId) {
        chrome.tabs.sendMessage(tabId, { type: "RECORDING_SAVED", store, stepCount: steps.length }).catch(() => {});
      }
      sendResponse({ steps });
    });
    return true;
  }

  if (msg.type === "DISMISS_OVERLAY") {
    chrome.storage.session.get({ rec: null }, ({ rec }) => {
      if (rec?.tabId) chrome.tabs.sendMessage(rec.tabId, { type: "RECORDING_STOP" }).catch(() => {});
      chrome.storage.session.remove("rec");
    });
  }

  if (msg.type === "REPLAY_STEPS") {
    replaySteps(msg.steps, msg.tabId);
    sendResponse({ ok: true });
    return true;
  }

  // ── Auto-capture on checkout ───────────────────────────────────────────────
  // When content.js detects a checkout page it sends us the page text.
  // If a weekly job is loaded we call Ollama, auto-detect basket + membership
  // and save — no popup interaction required.

  if (msg.type === "CHECKOUT_DETECTED") {
    const tabId = sender.tab?.id;
    // Always store context so popup can use it for manual capture fallback
    const ctx = { platform: msg.platform, retailer: msg.retailer, url: msg.url, tabId, ts: Date.now() };
    chrome.storage.session.set({ checkoutCtx: ctx });

    // Attempt fully automatic capture
    if (msg.pageText && tabId != null) {
      autoCapture(msg, tabId);
    } else {
      // No page text yet — just light up the badge for manual capture
      setBadge(tabId, "GO", "#4caf82");
    }
    return false;
  }

  // ── Captures storage ───────────────────────────────────────────────────────

  if (msg.type === "GET_CAPTURES") {
    chrome.storage.local.get({ weeklyCaptures: {} }, ({ weeklyCaptures }) => {
      chrome.storage.session.get({ checkoutCtx: null }, ({ checkoutCtx }) => {
        sendResponse({ captures: weeklyCaptures, checkoutCtx });
      });
    });
    return true;
  }

  if (msg.type === "CLEAR_CAPTURES") {
    chrome.storage.local.remove("weeklyCaptures", () => sendResponse({ ok: true }));
    return true;
  }
});

// ── Auto-capture logic ────────────────────────────────────────────────────────

async function autoCapture(msg, tabId) {
  const lockKey = `capturing_${tabId}`;
  if (await getSession(lockKey)) return;
  await setSession({ [lockKey]: true });

  setBadge(tabId, "…", "#7c6fe0");

  try {
    const settings = await getSettings();

    // Fast path: combo pinned after first capture — only extract fees.
    const pinned = await getSession(`pinned_${tabId}`);

    let result, retailerName, membership;

    if (pinned) {
      retailerName = pinned.retailerName;
      membership   = pinned.membership;
      const prompt = buildFastPrompt(msg.pageText, msg.url, msg.platform, retailerName, membership);
      result = await callOllama(settings.ollamaModel || "llama3.2", prompt, settings.ollamaApiKey || null);
    } else {
      // First capture: detect store + membership from page
      const prompt = buildAutoPrompt(msg.pageText, msg.url, msg.platform);
      result = await callOllama(settings.ollamaModel || "llama3.2", prompt, settings.ollamaApiKey || null);
      retailerName = result.store || msg.retailer || "Unknown Store";
      membership   = result.membership_status === "Member" ? "Member" : "Non-Member";
    }

    const itemTotal = parseFloat(String(result.item_total ?? "").replace(/[$,]/g, "")) || msg.subtotal || 0;
    const basket    = nearestBasket(itemTotal);
    if (!basket) {
      setBadge(tabId, "GO", "#4caf82");
      showTabToast(tabId, "⚠️ Could not read cart total — scroll down so all fees are visible, then refresh checkout", "err");
      return;
    }

    const key = jobKey(msg.platform, retailerName, basket, membership);

    const { weeklyCaptures } = await getLocal("weeklyCaptures");
    const allCaptures = weeklyCaptures || {};

    if (allCaptures[key]) {
      setBadge(tabId, "✓", "#4caf82");
      setTimeout(() => setBadge(tabId, "", "#4caf82"), 3000);
      showTabToast(tabId, `Already captured — $${basket} / ${membership}`, "ok");
      sendComboBanner(tabId, allCaptures, msg.platform, retailerName, membership);
      if (!pinned) await setSession({ [`pinned_${tabId}`]: { retailerName, membership } });
      return;
    }

    allCaptures[key] = {
      ...result,
      platform:     msg.platform,
      retailerName,
      membership,
      basketTarget: basket,
      capturedAt:   new Date().toISOString(),
    };
    await setLocal({ weeklyCaptures: allCaptures });
    await setSession({ checkoutCtx: null });

    const doneForCombo = BASKETS.filter(b => allCaptures[jobKey(msg.platform, retailerName, b, membership)]).length;
    setBadge(tabId, "✓", "#4caf82");
    setTimeout(() => setBadge(tabId, "", "#4caf82"), 3000);
    showTabToast(tabId, `✅ Saved $${basket} / ${membership}  (${doneForCombo}/5)`, "ok");

    await setSession({ [`pinned_${tabId}`]: { retailerName, membership } });
    sendComboBanner(tabId, allCaptures, msg.platform, retailerName, membership);

    const remaining = BASKETS.filter(b => !allCaptures[jobKey(msg.platform, retailerName, b, membership)]);

    if (remaining.length > 0) {
      setTimeout(() => {
        chrome.tabs.sendMessage(tabId, {
          type:            "BUILD_CART_TO_TARGET",
          target:          remaining[0],
          currentSubtotal: itemTotal,
          platform:        msg.platform,
        }).catch(() => {});
      }, 1500);
    } else {
      const other = membership === "Member" ? "Non-Member" : "Member";
      const allOtherDone = BASKETS.every(b => !!allCaptures[jobKey(msg.platform, retailerName, b, other)]);
      if (!allOtherDone) {
        await setSession({ [`pinned_${tabId}`]: { retailerName, membership: other } });
        showTabToast(tabId,
          `🎉 All ${membership} done! ${other === "Member" ? "Activate" : "Deactivate"} membership → build a $10 cart → go to checkout.`,
          "ok"
        );
      } else {
        await setSession({ [`pinned_${tabId}`]: null });
        showTabToast(tabId, `🏁 ${retailerName} complete on ${msg.platform}! Open popup → Export CSV.`, "ok");
      }
    }

  } catch (err) {
    setBadge(tabId, "GO", "#4caf82");
    console.error("[Fee Checker] Auto-capture:", err.message);
  } finally {
    await setSession({ [lockKey]: null });
  }
}

function sendComboBanner(tabId, allCaptures, platform, retailerName, membership) {
  const capturedBaskets  = BASKETS.filter(b =>  allCaptures[jobKey(platform, retailerName, b, membership)]);
  const remainingBaskets = BASKETS.filter(b => !allCaptures[jobKey(platform, retailerName, b, membership)]);
  chrome.tabs.sendMessage(tabId, {
    type: "SHOW_COMBO_BANNER",
    platform, retailerName, membership,
    capturedBaskets, remainingBaskets,
  }).catch(() => {});
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const BASKETS = [10, 25, 50, 75, 100];

function nearestBasket(itemTotal) {
  if (!itemTotal || isNaN(itemTotal) || itemTotal <= 0) return null;
  return BASKETS.reduce((a, b) => Math.abs(b - itemTotal) < Math.abs(a - itemTotal) ? b : a);
}

function jobKey(platform, retailerName, basket, membership) {
  return `${platform}|${retailerName}|${basket}|${membership}`.toLowerCase();
}

function setBadge(tabId, text, color) {
  try {
    chrome.action.setBadgeText({ text, tabId });
    chrome.action.setBadgeBackgroundColor({ color, tabId });
  } catch (e) {}
}

function showTabToast(tabId, text, style) {
  chrome.tabs.sendMessage(tabId, { type: "SHOW_TOAST", text, style }).catch(() => {});
}

// Storage promise wrappers
function getLocal(key)     { return new Promise(r => chrome.storage.local.get({ [key]: null }, d => r(d))); }
function setLocal(obj)     { return new Promise(r => chrome.storage.local.set(obj, r)); }
function getSession(key)   { return new Promise(r => chrome.storage.session.get({ [key]: null }, d => r(d[key]))); }
function setSession(obj)   { return new Promise(r => chrome.storage.session.set(obj, r)); }
function getSettings()     { return new Promise(r => chrome.storage.sync.get(["provider","ollamaModel","ollamaApiKey","geminiApiKey"], r)); }

// ── Ollama call (from service worker) ────────────────────────────────────────

async function callOllama(model, prompt, apiKey) {
  const base    = apiKey ? "https://ollama.com" : "http://localhost:11434";
  const headers = { "Content-Type": "application/json" };
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

  const resp = await fetch(`${base}/api/chat`, {
    method: "POST", headers,
    body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }], stream: false }),
  });
  if (!resp.ok) throw new Error(`Ollama ${resp.status}`);
  const data = await resp.json();
  const raw  = data?.message?.content || data.response || "";
  return JSON.parse(raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim());
}

// ── Prompts ───────────────────────────────────────────────────────────────────

// Fast prompt: store + membership already known (pinned), only extract fees.
function buildFastPrompt(pageText, url, platform, retailerName, membership) {
  return `You are extracting delivery fees from a ${platform} checkout page for ${retailerName}.
Membership: ${membership} (${membership === "Member" ? "active DashPass/Instacart+/Uber One" : "no membership"}).

Page URL: ${url}
Page content:
---
${pageText.slice(0, 10000)}
---

Return ONLY valid JSON — no markdown, no explanation:
{
  "item_total": "<merchandise subtotal before fees>",
  "delivery_fee": "<delivery fee or '0' if free>",
  "service_fee": "<platform service fee>",
  "regulatory_fee": "<regulatory fee or null>",
  "long_distance_fee": "<long distance fee or null>",
  "small_order_fee": "<small order fee or null>",
  "taxes": "<total taxes>",
  "order_total": "<grand total>",
  "delivery_time_min": <minimum delivery minutes as integer>,
  "delivery_time_max": <maximum delivery minutes as integer>,
  "priority_delivery_time": <priority option minutes or null>,
  "priority_delivery_fee": "<priority extra cost or null>",
  "minimum_order": "<minimum order or null>",
  "notes": "<brief observation or null>"
}
Numbers as strings without $ signs. null for anything not visible.`;
}

function buildAutoPrompt(pageText, url, platform) {
  return `You are extracting delivery fee data from a ${platform} checkout page.

Page URL: ${url}
Page content:
---
${pageText.slice(0, 10000)}
---

Return ONLY valid JSON — no markdown, no explanation:
{
  "store": "<retailer name exactly as shown on page>",
  "membership_status": "<'Member' if DashPass/Instacart+/Uber One is shown as ACTIVE with delivery benefits (e.g. $0 delivery fee labeled as membership benefit, DashPass checkmark), otherwise 'Non-Member'>",
  "item_total": "<merchandise subtotal before fees, numbers only no $ sign>",
  "delivery_fee": "<delivery fee, '0' if free>",
  "service_fee": "<platform/service fee>",
  "regulatory_fee": "<regulatory fee or null>",
  "long_distance_fee": "<long distance fee or null>",
  "small_order_fee": "<small order fee or null>",
  "taxes": "<total taxes>",
  "order_total": "<grand total>",
  "delivery_time_min": "<minimum delivery time in minutes as integer>",
  "delivery_time_max": "<maximum delivery time in minutes as integer>",
  "priority_delivery_time": "<priority option minutes or null>",
  "priority_delivery_fee": "<priority delivery extra cost or null>",
  "minimum_order": "<minimum order requirement or null>",
  "notes": "<brief notable observation or null>"
}
Use null for anything not visible. Numbers as strings without $ signs.`;
}

// ── Replay ─────────────────────────────────────────────────────────────────────

async function replaySteps(steps, tabId) {
  for (const step of steps) {
    if (step.type === "navigate") {
      await new Promise(resolve => {
        chrome.tabs.update(tabId, { url: step.url }, () => {
          const listener = (id, info) => {
            if (id === tabId && info.status === "complete") {
              chrome.tabs.onUpdated.removeListener(listener);
              setTimeout(resolve, 800);
            }
          };
          chrome.tabs.onUpdated.addListener(listener);
        });
      });
    }
    if (step.type === "click") {
      await new Promise(r => setTimeout(r, 600));
      chrome.scripting.executeScript({
        target: { tabId },
        func: (selector, text) => {
          let el = selector ? document.querySelector(selector) : null;
          if (!el && text) {
            const all = document.querySelectorAll("button, a, [role='button'], [role='link'], li");
            el = Array.from(all).find(e => e.innerText?.trim().startsWith(text.slice(0, 30)));
          }
          if (el) { el.scrollIntoView({ block: "center" }); el.click(); return true; }
          return false;
        },
        args: [step.selector, step.text],
      }).catch(() => {});
      await new Promise(r => setTimeout(r, 1000));
    }
  }
  chrome.tabs.sendMessage(tabId, { type: "REPLAY_DONE" }).catch(() => {});
}
