"use strict";

const saveBtn   = document.getElementById("save-btn");
const status    = document.getElementById("status");
const toggleBtn = document.getElementById("toggle-vis");
const apiKeyInput    = document.getElementById("api-key");
const ollamaModelInput = document.getElementById("ollama-model");
const tabBtns   = document.querySelectorAll(".tab-btn");

let activeProvider = "ollama";

// ── Load saved settings ───────────────────────────────────────────────────────
chrome.storage.sync.get(["provider", "ollamaModel", "geminiApiKey"], (data) => {
  activeProvider = data.provider || "ollama";
  setProvider(activeProvider);
  if (data.ollamaModel) ollamaModelInput.value = data.ollamaModel;
  if (data.geminiApiKey) apiKeyInput.value = data.geminiApiKey;
});

// ── Provider tabs ─────────────────────────────────────────────────────────────
function setProvider(p) {
  activeProvider = p;
  tabBtns.forEach(btn => btn.classList.toggle("active", btn.dataset.provider === p));
  document.getElementById("section-ollama").classList.toggle("active", p === "ollama");
  document.getElementById("section-gemini").classList.toggle("active", p === "gemini");
}

tabBtns.forEach(btn => btn.addEventListener("click", () => setProvider(btn.dataset.provider)));

// ── Show/hide API key ─────────────────────────────────────────────────────────
toggleBtn?.addEventListener("click", () => {
  const hidden = apiKeyInput.type === "password";
  apiKeyInput.type = hidden ? "text" : "password";
  toggleBtn.textContent = hidden ? "Hide" : "Show";
});

// ── Save ──────────────────────────────────────────────────────────────────────
saveBtn.addEventListener("click", () => {
  const data = { provider: activeProvider };

  if (activeProvider === "ollama") {
    data.ollamaModel = ollamaModelInput.value.trim() || "llama3.2";
  } else {
    const key = apiKeyInput.value.trim();
    if (!key) { status.textContent = "Please enter an API key."; status.className = "error"; return; }
    data.geminiApiKey = key;
  }

  chrome.storage.sync.set(data, () => {
    status.textContent = "Saved!";
    status.className = "";
    setTimeout(() => { status.textContent = ""; }, 2000);
  });
});
