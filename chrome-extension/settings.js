"use strict";

const keyInput  = document.getElementById("api-key");
const saveBtn   = document.getElementById("save-btn");
const toggleBtn = document.getElementById("toggle-vis");
const status    = document.getElementById("status");

// Load saved key on open
chrome.storage.sync.get("geminiApiKey", ({ geminiApiKey }) => {
  if (geminiApiKey) keyInput.value = geminiApiKey;
});

// Show / hide key
toggleBtn.addEventListener("click", () => {
  const hidden = keyInput.type === "password";
  keyInput.type  = hidden ? "text" : "password";
  toggleBtn.textContent = hidden ? "Hide" : "Show";
});

// Save
saveBtn.addEventListener("click", () => {
  const key = keyInput.value.trim();
  if (!key) {
    status.textContent = "Please enter an API key.";
    status.className = "error";
    return;
  }
  chrome.storage.sync.set({ geminiApiKey: key }, () => {
    status.textContent = "Saved!";
    status.className = "";
    setTimeout(() => { status.textContent = ""; }, 2000);
  });
});
