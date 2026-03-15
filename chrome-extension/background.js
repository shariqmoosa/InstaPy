"use strict";

// Manages recording state across popup open/close cycles.

let state = {
  recording: false,
  steps: [],
  store: "",
  tabId: null,
};

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

  if (msg.type === "RECORDING_STATE") {
    sendResponse({ ...state });
    return true;
  }

  if (msg.type === "START_RECORDING") {
    state = { recording: true, steps: [], store: msg.store, tabId: msg.tabId };
    // Tell the content script on that tab to start
    chrome.tabs.sendMessage(msg.tabId, { type: "RECORDING_START" });
    sendResponse({ ok: true });
    return true;
  }

  if (msg.type === "RECORD_STEP") {
    if (state.recording) state.steps.push(msg.step);
    // Broadcast updated count to any open popup
    chrome.runtime.sendMessage({ type: "STEP_RECORDED", count: state.steps.length }).catch(() => {});
    return true;
  }

  if (msg.type === "STOP_RECORDING") {
    state.recording = false;
    const { store, steps } = state;
    if (steps.length > 0) {
      chrome.storage.local.get({ recordings: {} }, ({ recordings }) => {
        recordings[store] = { steps, savedAt: new Date().toISOString() };
        chrome.storage.local.set({ recordings });
      });
    }
    // Tell content script to remove overlay
    if (state.tabId) {
      chrome.tabs.sendMessage(state.tabId, { type: "RECORDING_STOP" }).catch(() => {});
    }
    sendResponse({ steps });
    return true;
  }

  if (msg.type === "REPLAY_STEPS") {
    replaySteps(msg.steps, msg.tabId);
    sendResponse({ ok: true });
    return true;
  }
});

// ── Replay ────────────────────────────────────────────────────────────────────

async function replaySteps(steps, tabId) {
  for (const step of steps) {
    if (step.type === "navigate") {
      await new Promise(resolve => {
        chrome.tabs.update(tabId, { url: step.url }, () => {
          // Wait for page to load
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
      await new Promise(resolve => setTimeout(resolve, 600));
      chrome.scripting.executeScript({
        target: { tabId },
        func: (selector, text) => {
          // Try selector first
          let el = selector ? document.querySelector(selector) : null;
          // Fallback: find by text content
          if (!el && text) {
            const all = document.querySelectorAll("button, a, [role='button'], [role='link'], li");
            el = Array.from(all).find(e => e.innerText?.trim().startsWith(text.slice(0, 30)));
          }
          if (el) { el.scrollIntoView({ block: "center" }); el.click(); return true; }
          return false;
        },
        args: [step.selector, step.text],
      }).catch(() => {});
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  // Signal replay complete
  chrome.tabs.sendMessage(tabId, { type: "REPLAY_DONE" }).catch(() => {});
}
