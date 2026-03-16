"use strict";

const saveBtn   = document.getElementById("save-btn");
const status    = document.getElementById("status");
const toggleBtn = document.getElementById("toggle-vis");
const apiKeyInput    = document.getElementById("api-key");
const ollamaModelInput  = document.getElementById("ollama-model");
const ollamaApiKeyInput = document.getElementById("ollama-api-key");
const tabBtns   = document.querySelectorAll(".tab-btn");

let activeProvider = "ollama";

// ── Load saved settings ───────────────────────────────────────────────────────
chrome.storage.sync.get(["provider", "ollamaModel", "ollamaApiKey", "geminiApiKey"], (data) => {
  activeProvider = data.provider || "ollama";
  setProvider(activeProvider);
  if (data.ollamaModel)  ollamaModelInput.value  = data.ollamaModel;
  if (data.ollamaApiKey) ollamaApiKeyInput.value = data.ollamaApiKey;
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

// ── Save with validation ──────────────────────────────────────────────────────
saveBtn.addEventListener("click", async () => {
  const data = { provider: activeProvider };
  saveBtn.disabled = true;
  status.className = "";
  status.textContent = "Testing connection…";

  try {
    if (activeProvider === "ollama") {
      data.ollamaModel  = ollamaModelInput.value.trim() || "llama3.2";
      data.ollamaApiKey = ollamaApiKeyInput.value.trim();
      await testOllama(data.ollamaModel, data.ollamaApiKey);
    } else {
      const key = apiKeyInput.value.trim();
      if (!key) { status.textContent = "Please enter an API key."; status.className = "error"; return; }
      data.geminiApiKey = key;
      await testGemini(key);
    }

    chrome.storage.sync.set(data, () => {
      status.textContent = "✅ Connected and saved!";
      status.className = "";
      setTimeout(() => { status.textContent = ""; }, 3000);
    });
  } catch (err) {
    status.textContent = err.message;
    status.className = "error";
  } finally {
    saveBtn.disabled = false;
  }
});

async function testOllama(model, apiKey) {
  const base    = apiKey ? "https://ollama.com" : "http://localhost:11434";
  const headers = { "Content-Type": "application/json" };
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
  let resp;
  try {
    resp = await fetch(`${base}/api/chat`, {
      method: "POST", headers,
      body: JSON.stringify({ model, messages: [{ role: "user", content: "Reply with the single word: ok" }], stream: false }),
    });
  } catch (e) {
    throw new Error(apiKey ? "Cannot reach Ollama cloud. Check your API key." : "Cannot reach local Ollama. Run: ollama serve");
  }
  if (resp.status === 404) throw new Error(`Model "${model}" not found. Check the model name.`);
  if (!resp.ok) {
    const txt = await resp.text().catch(() => "");
    throw new Error(`Ollama error ${resp.status}: ${txt.slice(0, 100)}`);
  }
}

async function testGemini(apiKey) {
  const url  = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${apiKey}`;
  let resp;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contents: [{ parts: [{ text: "Reply with the single word: ok" }] }] }),
    });
  } catch (e) {
    throw new Error("Cannot reach Gemini API. Check your internet connection.");
  }
  if (resp.status === 400 || resp.status === 403) throw new Error("Invalid Gemini API key.");
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(`Gemini error: ${err?.error?.message || `HTTP ${resp.status}`}`);
  }
}
