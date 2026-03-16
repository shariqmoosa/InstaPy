"use strict";

const $ = (id) => document.getElementById(id);

// ── Tab switching ─────────────────────────────────────────────────────────────

const TAB_IDS = { weekly: "tab-weekly", analyze: "tab-analyze", record: "tab-record" };

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");
    Object.values(TAB_IDS).forEach(id => { const el = $(id); if (el) el.style.display = "none"; });
    const tabEl = $(TAB_IDS[btn.dataset.tab]);
    if (tabEl) tabEl.style.display = "block";
    if (btn.dataset.tab === "weekly")   refreshWeekly();
    if (btn.dataset.tab === "record")   renderRecordings();
  });
});

// Show Weekly tab by default
$("tab-weekly").style.display = "block";

// ══════════════════════════════════════════════════════════════════════════════
// WEEKLY TAB
// ══════════════════════════════════════════════════════════════════════════════

const BASKETS    = [10, 25, 50, 75, 100];
const PLAT_SHORT = { "DoorDash": "DD", "Instacart": "IC", "Uber Eats": "UE" };

let _captures = {};

async function refreshWeekly() {
  const resp = await chrome.runtime.sendMessage({ type: "GET_CAPTURES" });
  _captures = resp?.captures || {};
  renderWeekly();
}

function renderWeekly() {
  const hasData = Object.keys(_captures).length > 0;
  $("weekly-empty").style.display = hasData ? "none"  : "block";
  $("weekly-job").style.display   = hasData ? "block" : "none";
  if (hasData) renderSessions();
}

function renderSessions() {
  // Group by platform + retailerName + membership using stored fields
  const groups = {};
  for (const c of Object.values(_captures)) {
    if (!c.platform) continue;
    const gk = `${c.platform}|${c.retailerName}|${c.membership}`;
    if (!groups[gk]) groups[gk] = { platform: c.platform, retailerName: c.retailerName, membership: c.membership, baskets: [] };
    if (c.basketTarget) groups[gk].baskets.push(Number(c.basketTarget));
  }

  const total = Object.keys(_captures).length;
  $("weekly-progress-text").textContent = `${total} capture${total !== 1 ? "s" : ""}`;

  $("sessions-list").innerHTML = Object.values(groups).map(g => {
    const plat  = PLAT_SHORT[g.platform] || g.platform;
    const chips = BASKETS.map(b => {
      const done = g.baskets.includes(b);
      const style = done
        ? "color:#4caf82;font-weight:700"
        : "color:#333";
      return `<span style="${style}">$${b}${done ? "✓" : ""}</span>`;
    }).join('<span style="color:#222;padding:0 2px">·</span>');
    return `<div class="session-row">
      <div class="session-header">${plat} · ${g.retailerName} · ${g.membership}</div>
      <div class="session-baskets">${chips}</div>
    </div>`;
  }).join("");
}

// ── Export CSV ────────────────────────────────────────────────────────────────

$("export-btn").addEventListener("click", exportCaptures);

function exportCaptures() {
  if (!Object.keys(_captures).length) return;

  const COLS = [
    "Platform", "Retailer", "Membership", "Basket Target ($)",
    "Date Collected", "Item Total ($)", "Delivery Fee ($)",
    "Service Fee ($)", "Regulatory Fee ($)", "Long Distance Fee ($)",
    "Small Order Fee ($)", "Taxes ($)", "Order Total ($)",
    "Min Delivery Time (min)", "Max Delivery Time (min)",
    "Priority Delivery Time (min)", "Priority Delivery Fee ($)",
    "Minimum Order ($)", "Notes",
  ];

  const rows = [COLS];
  for (const c of Object.values(_captures)) {
    rows.push([
      c.platform || "", c.retailerName || "", c.membership || "", c.basketTarget || "",
      c.capturedAt ? c.capturedAt.slice(0, 10) : "",
      c.item_total || "", c.delivery_fee || "", c.service_fee || "",
      c.regulatory_fee || "", c.long_distance_fee || "",
      c.small_order_fee || "", c.taxes || "", c.order_total || "",
      c.delivery_time_min || "", c.delivery_time_max || "",
      c.priority_delivery_time || "", c.priority_delivery_fee || "",
      c.minimum_order || "", c.notes || "",
    ]);
  }

  const csv  = rows.map(row => row.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `fee-data-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Clear ─────────────────────────────────────────────────────────────────────

$("clear-job-btn").addEventListener("click", async () => {
  if (!confirm("Clear all captured data? This cannot be undone.")) return;
  await chrome.runtime.sendMessage({ type: "CLEAR_CAPTURES" });
  _captures = {};
  renderWeekly();
});

// ── AI callers (shared with Analyze tab) ──────────────────────────────────────

async function callGemini(apiKey, prompt) {
  const url  = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${apiKey}`;
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
    throw new Error(`Gemini error: ${err?.error?.message || `HTTP ${resp.status}`}`);
  }
  const data = await resp.json();
  const raw  = data?.candidates?.[0]?.content?.parts?.[0]?.text || "";
  return JSON.parse(raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim());
}

async function callOllama(model, prompt, apiKey) {
  const baseUrl = apiKey ? "https://ollama.com" : "http://localhost:11434";
  const headers = { "Content-Type": "application/json" };
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
  let resp;
  try {
    resp = await fetch(`${baseUrl}/api/chat`, {
      method: "POST", headers,
      body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }], stream: false }),
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
  const raw  = data?.message?.content || data.response || "";
  return JSON.parse(raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim());
}

// ══════════════════════════════════════════════════════════════════════════════
// ANALYZE TAB  (unchanged behaviour, now includes $10 chip)
// ══════════════════════════════════════════════════════════════════════════════

let analyzeSelectedSize = null;

document.querySelectorAll("#analyze-basket-chips .bchip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#analyze-basket-chips .bchip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    analyzeSelectedSize = Number(chip.dataset.size);
  });
});

function showEl(id, mode) { const el = $(id); if (el) el.style.display = mode || "block"; }
function hideEl(...ids)   { ids.forEach(id => { const el = $(id); if (el) el.style.display = "none"; }); }

function showError(msg) {
  hideEl("loading", "results");
  $("error-box").textContent = msg;
  showEl("error-box");
  $("analyze-btn").disabled = false;
}

function buildAnalyzePrompt(pageText, url) {
  return `You are analyzing a grocery or food-delivery checkout page. Extract ALL fees and delivery information visible on the page.

Page URL: ${url}
Page content:
---
${pageText}
---

Return ONLY a valid JSON object — no markdown, no explanation:
{
  "store": "<retailer/brand name>",
  "item_total": "<subtotal before fees or null>",
  "delivery_fee": "<delivery fee or 'Free' or null>",
  "service_fee": "<service or platform fee or null>",
  "taxes": "<tax amount or null>",
  "tip": "<tip or null>",
  "delivery_time": "<estimated delivery time e.g. '30-45 min' or null>",
  "other_fees": [{"name": "<fee name>", "amount": "<amount>"}],
  "order_total": "<grand total or null>",
  "notes": "<short caveat if any or null>"
}
Use null for anything not visible.`;
}

async function saveAnalyzeCapture(result, size) {
  if (!result.store) return;
  const store = result.store.trim();
  const entry = {
    delivery_fee:  result.delivery_fee  || null,
    service_fee:   result.service_fee   || null,
    taxes:         result.taxes         || null,
    delivery_time: result.delivery_time || null,
    order_total:   result.order_total   || null,
    capturedAt:    new Date().toISOString(),
  };
  if (result.other_fees?.length) entry.other_fees = result.other_fees;

  const { captures = {} } = await chrome.storage.local.get("captures");
  if (!captures[store]) captures[store] = {};
  if (size) captures[store][size] = entry;
  await chrome.storage.local.set({ captures });

  const tableUrl   = chrome.runtime.getURL("table.html");
  const [existing] = await chrome.tabs.query({ url: tableUrl });
  if (existing) chrome.tabs.update(existing.id, { active: true });
  else chrome.tabs.create({ url: tableUrl, active: false });
}

function renderAnalyzeResults(result) {
  $("store-name").textContent = result.store || "Fee Breakdown";
  const rows = [
    ["Items",        result.item_total],
    ["Delivery",     result.delivery_fee],
    ["Service fee",  result.service_fee],
    ["Taxes",        result.taxes],
    ["Tip",          result.tip],
    ["Est. time",    result.delivery_time],
    ...(result.other_fees || []).map(f => [f.name, f.amount]),
  ];
  const tbody = $("fee-table");
  tbody.innerHTML = "";
  for (const [label, amount] of rows) {
    const isFree = amount && /\b(free|£0|€0|\$0|0\.00)\b/i.test(amount);
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="label">${label}</td>
      <td class="${amount ? (isFree ? "amount badge-free" : "amount") : "null-amount"}">
        ${amount ? (isFree ? "Free" : amount) : "—"}</td>`;
    tbody.appendChild(tr);
  }
  if (result.order_total) {
    const tr = document.createElement("tr");
    tr.className = "total-row";
    tr.innerHTML = `<td class="label">Total</td><td class="amount">${result.order_total}</td>`;
    tbody.appendChild(tr);
  }
  const notesEl = $("notes");
  notesEl.style.display = result.notes ? "block" : "none";
  if (result.notes) notesEl.textContent = result.notes;

  const copyBtn    = $("copy-row-btn");
  const sizeLabel  = analyzeSelectedSize ? `$${analyzeSelectedSize}` : "(no size)";
  copyBtn.textContent  = `📋 Copy row for Excel  ·  ${result.store || "store"} ${sizeLabel}`;
  copyBtn.dataset.result = JSON.stringify(result);
  copyBtn.classList.remove("copied");

  hideEl("loading", "error-box", "analyze-btn");
  showEl("results");
}

$("copy-row-btn").addEventListener("click", () => {
  const result = JSON.parse($("copy-row-btn").dataset.result || "{}");
  const cols = [
    result.store || "", analyzeSelectedSize ? `$${analyzeSelectedSize}` : "",
    result.delivery_fee || "", result.service_fee || "",
    result.taxes || "", result.delivery_time || "",
    result.tip || "", result.order_total || "",
  ];
  navigator.clipboard.writeText(cols.join("\t")).then(() => {
    $("copy-row-btn").textContent = "✅ Copied!";
    $("copy-row-btn").classList.add("copied");
    setTimeout(() => {
      $("copy-row-btn").textContent = "📋 Copy row for Excel";
      $("copy-row-btn").classList.remove("copied");
    }, 2000);
  });
});

async function runAnalyze() {
  const settings = await chrome.storage.sync.get(["provider", "ollamaModel", "ollamaApiKey", "geminiApiKey"]);
  const [tab]    = await chrome.tabs.query({ active: true, currentWindow: true });
  const url      = tab?.url || "";
  const hostname = url ? new URL(url).hostname.replace(/^www\./, "") : "—";
  $("site-label").textContent = hostname;

  const provider = settings.provider || "ollama";
  if (provider === "gemini" && !settings.geminiApiKey) { hideEl("analyze-btn"); showEl("no-key"); return; }

  $("analyze-btn").addEventListener("click", async () => {
    $("analyze-btn").disabled = true;
    hideEl("results", "error-box");
    showEl("loading", "flex");
    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_DATA" });
      if (!response?.pageText) throw new Error("Could not read page. Try refreshing.");
      const prompt = buildAnalyzePrompt(response.pageText, response.url);
      const result = provider === "ollama"
        ? await callOllama(settings.ollamaModel || "llama3.2", prompt, settings.ollamaApiKey)
        : await callGemini(settings.geminiApiKey, prompt);
      renderAnalyzeResults(result);
      await saveAnalyzeCapture(result, analyzeSelectedSize);
    } catch (err) {
      showError(err.message || "Something went wrong.");
    }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// RECORDINGS TAB  (unchanged)
// ══════════════════════════════════════════════════════════════════════════════

async function renderRecordings() {
  const { recordings = {} } = await chrome.storage.local.get("recordings");
  const list   = $("recordings-list");
  const stores = Object.keys(recordings);
  if (!stores.length) {
    list.innerHTML = `<div class="rec-empty">No recordings yet.</div>`;
    return;
  }
  list.innerHTML = stores.map(store => {
    const rec  = recordings[store];
    const date = new Date(rec.savedAt).toLocaleDateString();
    return `<div class="rec-item">
      <div>
        <div class="rec-store">${store}</div>
        <div class="rec-meta">${rec.steps.length} steps · ${date}</div>
      </div>
      <div class="rec-actions">
        <button class="rec-btn rec-btn-play" data-store="${store}">▶ Run</button>
        <button class="rec-btn rec-btn-delete" data-store="${store}">✕</button>
      </div>
    </div>`;
  }).join("");

  list.querySelectorAll(".rec-btn-play").forEach(btn => {
    btn.addEventListener("click", () => replayRecording(btn.dataset.store, recordings));
  });
  list.querySelectorAll(".rec-btn-delete").forEach(btn => {
    btn.addEventListener("click", async () => {
      delete recordings[btn.dataset.store];
      await chrome.storage.local.set({ recordings });
      renderRecordings();
    });
  });
}

async function replayRecording(store, recordings) {
  const [tab]  = await chrome.tabs.query({ active: true, currentWindow: true });
  const steps  = recordings[store].steps;
  $("record-btn").disabled     = true;
  $("record-btn").textContent  = "▶ Replaying…";
  await chrome.runtime.sendMessage({ type: "REPLAY_STEPS", steps, tabId: tab.id });
  await new Promise(r => setTimeout(r, 2000));
  $("record-btn").disabled     = false;
  $("record-btn").textContent  = "🔴 Start Recording";
  document.querySelector('[data-tab="analyze"]').click();
  $("analyze-btn").click();
}

$("record-btn").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const store = new URL(tab.url).hostname.replace(/^www\./, "");
  await chrome.runtime.sendMessage({ type: "START_RECORDING", store, tabId: tab.id });
  window.close();
});

// ══════════════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════════════

$("settings-btn").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("open-settings")?.addEventListener("click", () => chrome.runtime.openOptionsPage());

// Boot: load weekly job, then set up analyze tab
refreshWeekly().then(() => runAnalyze());
