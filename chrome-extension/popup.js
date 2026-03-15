"use strict";

const GEMINI_MODEL = "gemini-1.5-flash";
const GEMINI_URL = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`;

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

// ── Gemini API ────────────────────────────────────────────────────────────────

async function callGemini(apiKey, pageText, url) {
  const prompt = `You are analyzing a shopping or food-delivery page to find ALL fees a customer would pay.

Page URL: ${url}
Page content:
---
${pageText}
---

Return ONLY a valid JSON object — no markdown, no explanation — with this exact shape:
{
  "store": "<store or restaurant name, or null>",
  "item_total": "<subtotal of items, or null>",
  "delivery_fee": "<delivery fee, or null>",
  "service_fee": "<service or platform fee, or null>",
  "taxes": "<tax amount or percentage, or null>",
  "tip": "<tip amount or options, or null>",
  "other_fees": [{"name": "<fee name>", "amount": "<amount>"}],
  "order_total": "<grand total if shown, or null>",
  "notes": "<any important caveats, promotions, or missing info — keep short>"
}

Rules:
- If a value is not visible on the page, use null (not "N/A" or "unknown").
- other_fees should only contain fees not already captured above (e.g. small order fee, bag fee).
- amounts should include the currency symbol if shown.`;

  const resp = await fetch(`${GEMINI_URL}?key=${apiKey}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { temperature: 0.1, maxOutputTokens: 512 },
    }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const msg = err?.error?.message || `HTTP ${resp.status}`;
    throw new Error(`Gemini API error: ${msg}`);
  }

  const data = await resp.json();
  const raw = data?.candidates?.[0]?.content?.parts?.[0]?.text || "";

  // Strip markdown code fences if present
  const cleaned = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
  return JSON.parse(cleaned);
}

// ── Render results ────────────────────────────────────────────────────────────

function renderResults(result) {
  $("store-name").textContent = result.store || "Fee Breakdown";

  const rows = [
    ["Items",        result.item_total],
    ["Delivery",     result.delivery_fee],
    ["Service fee",  result.service_fee],
    ["Taxes",        result.taxes],
    ["Tip",          result.tip],
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
  const { geminiApiKey } = await chrome.storage.sync.get("geminiApiKey");

  // Get current tab info
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || "";
  const hostname = url ? new URL(url).hostname.replace(/^www\./, "") : "—";
  $("site-label").textContent = hostname;

  if (!geminiApiKey) {
    hide("analyze-btn");
    show("no-key");
    return;
  }

  $("analyze-btn").addEventListener("click", async () => {
    $("analyze-btn").disabled = true;
    hide("results", "error-box");
    show("loading");

    try {
      // Ask content script for page text
      const response = await chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_DATA" });
      if (!response?.pageText) throw new Error("Could not read page content.");

      const result = await callGemini(geminiApiKey, response.pageText, response.url);
      renderResults(result);
    } catch (err) {
      showError(err.message || "Something went wrong.");
    }
  });
}

// ── Settings button + link ────────────────────────────────────────────────────

$("settings-btn").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("open-settings")?.addEventListener("click",  () => chrome.runtime.openOptionsPage());

main();
