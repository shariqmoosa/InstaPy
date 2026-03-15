"use strict";

const $ = (id) => document.getElementById(id);

// ── UI helpers ────────────────────────────────────────────────────────────────

function show(...ids) { ids.forEach(id => { $(id).style.display = id === "loading" ? "flex" : "block"; }); }
function hide(...ids) { ids.forEach(id => { $(id).style.display = "none"; }); }

function showError(msg) {
  hide("loading", "results");
  $("error-box").textContent = msg;
  show("error-box");
  $("analyze-btn").disabled = false;
}

// ── Ollama API ────────────────────────────────────────────────────────────────

async function callOllama(model, prompt, apiKey) {
  const baseUrl = apiKey ? "https://ollama.com" : "http://localhost:11434";
  const headers = { "Content-Type": "application/json" };
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

  let resp;
  try {
    resp = await fetch(`${baseUrl}/api/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model,
        messages: [{ role: "user", content: prompt }],
        stream: false,
      }),
    });
  } catch (e) {
    throw new Error(apiKey
      ? "Could not reach Ollama cloud. Check your API key and model name."
      : "Could not reach local Ollama. Is it running? (ollama serve)");
  }

  if (!resp.ok) {
    const txt = await resp.text().catch(() => "");
    throw new Error(`Ollama error ${resp.status}: ${txt.slice(0, 120)}`);
  }
  const data = await resp.json();
  const raw = data?.message?.content || data.response || "";
  const cleaned = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
  return JSON.parse(cleaned);
}

// ── Gemini API ────────────────────────────────────────────────────────────────

async function callGemini(apiKey, prompt) {
  const model = "gemini-2.0-flash";
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { temperature: 0.1, maxOutputTokens: 512 },
    }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(`Gemini API error: ${err?.error?.message || `HTTP ${resp.status}`}`);
  }
  const data = await resp.json();
  const raw = data?.candidates?.[0]?.content?.parts?.[0]?.text || "";
  const cleaned = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
  return JSON.parse(cleaned);
}

// ── Shared prompt ─────────────────────────────────────────────────────────────

function buildPrompt(pageText, url) {
  return `You are analyzing a shopping or food-delivery page to find ALL fees.

Page URL: ${url}
Page content:
---
${pageText}
---

Return ONLY a valid JSON object — no markdown, no explanation:
{
  "store": "<store name or null>",
  "item_total": "<subtotal or null>",
  "delivery_fee": "<delivery fee or null>",
  "service_fee": "<service/platform fee or null>",
  "taxes": "<tax amount or null>",
  "tip": "<tip or null>",
  "other_fees": [{"name": "<fee name>", "amount": "<amount>"}],
  "order_total": "<grand total or null>",
  "notes": "<short caveats or null>"
}
If a value is not visible, use null.`;
}

// ── Render results ────────────────────────────────────────────────────────────

function renderResults(result) {
  $("store-name").textContent = result.store || "Fee Breakdown";

  const rows = [
    ["Items",       result.item_total],
    ["Delivery",    result.delivery_fee],
    ["Service fee", result.service_fee],
    ["Taxes",       result.taxes],
    ["Tip",         result.tip],
    ...(result.other_fees || []).map(f => [f.name, f.amount]),
  ];

  const tbody = $("fee-table");
  tbody.innerHTML = "";

  for (const [label, amount] of rows) {
    const tr = document.createElement("tr");
    const isFree = amount && /\b(free|£0|€0|\$0|0\.00)\b/i.test(amount);
    tr.innerHTML = `
      <td class="label">${label}</td>
      <td class="${amount ? (isFree ? "amount badge-free" : "amount") : "null-amount"}">
        ${amount ? (isFree ? "Free" : amount) : "—"}
      </td>`;
    tbody.appendChild(tr);
  }

  if (result.order_total) {
    const tr = document.createElement("tr");
    tr.className = "total-row";
    tr.innerHTML = `<td class="label">Total</td><td class="amount">${result.order_total}</td>`;
    tbody.appendChild(tr);
  }

  const notesEl = $("notes");
  if (result.notes) {
    notesEl.textContent = result.notes;
    notesEl.style.display = "block";
  } else {
    notesEl.style.display = "none";
  }

  hide("loading", "error-box", "analyze-btn");
  show("results");
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  const data = await chrome.storage.sync.get(["provider", "ollamaModel", "ollamaApiKey", "geminiApiKey"]);
  const { provider, ollamaModel, geminiApiKey } = data;

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || "";
  const hostname = url ? new URL(url).hostname.replace(/^www\./, "") : "—";
  $("site-label").textContent = hostname;

  const activeProvider = provider || "ollama";

  if (activeProvider === "gemini" && !geminiApiKey) {
    hide("analyze-btn"); show("no-key"); return;
  }

  $("analyze-btn").addEventListener("click", async () => {
    $("analyze-btn").disabled = true;
    hide("results", "error-box");
    show("loading");

    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_DATA" });
      if (!response?.pageText) throw new Error("Could not read page content. Try refreshing the page.");

      const prompt = buildPrompt(response.pageText, response.url);
      let result;

      if (activeProvider === "ollama") {
        const model = ollamaModel || "llama3.2";
        result = await callOllama(model, prompt, data.ollamaApiKey);
      } else {
        result = await callGemini(geminiApiKey, prompt);
      }

      renderResults(result);
    } catch (err) {
      showError(err.message || "Something went wrong.");
    }
  });
}

$("settings-btn").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("open-settings")?.addEventListener("click", () => chrome.runtime.openOptionsPage());

main();
