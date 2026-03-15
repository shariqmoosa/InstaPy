"use strict";

// All recording state is written to chrome.storage.session so it survives
// service worker restarts (MV3 workers are killed after ~30s of inactivity).

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

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
      // Update overlay counter in the tab
      chrome.tabs.sendMessage(rec.tabId, { type: "UPDATE_COUNT", count: rec.steps.length }).catch(() => {});
    });
    return true;
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
      // Show "done" state in overlay
      if (tabId) {
        chrome.tabs.sendMessage(tabId, { type: "RECORDING_SAVED", store, stepCount: steps.length }).catch(() => {});
      }
      sendResponse({ steps });
    });
    return true;
  }

  if (msg.type === "DISMISS_OVERLAY") {
    chrome.storage.session.get({ rec: null }, ({ rec }) => {
      if (rec?.tabId) {
        chrome.tabs.sendMessage(rec.tabId, { type: "RECORDING_STOP" }).catch(() => {});
      }
      chrome.storage.session.remove("rec");
    });
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
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  chrome.tabs.sendMessage(tabId, { type: "REPLAY_DONE" }).catch(() => {});
}
