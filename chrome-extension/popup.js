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

const PLATFORMS  = ["DoorDash", "Instacart", "Uber Eats"];
const BASKETS    = [10, 25, 50, 75, 100];
const MEMBERSHIPS = ["Member", "Non-Member"];

// Platform short labels for the queue display
const PLAT_SHORT = { "DoorDash": "DD", "Instacart": "IC", "Uber Eats": "UE" };

let _job = null;          // full job object from storage
let _ctx = null;          // current checkout context from content script
let _basket = null;       // selected basket size (number)
let _membership = "Member";

// ── CSV parsing ───────────────────────────────────────────────────────────────

function parseSections(raw) {
  // Split on blank lines to find sections
  const sections = raw.trim().split(/\n\s*\n/).map(s => s.trim()).filter(Boolean);

  let retailerSection = null;
  let addressSection  = null;

  for (const sec of sections) {
    const firstLine = sec.split("\n")[0].toLowerCase();
    if (firstLine.includes("delivery address")) {
      addressSection = sec;
    } else if (firstLine.includes("retailer")) {
      retailerSection = sec;
    }
  }

  if (!retailerSection) throw new Error("Could not find retailer section (needs a 'Retailer Name' or 'Retailer' column header).");

  // Detect delimiter: tab or comma
  const delim = (retailerSection.split("\n")[0].includes("\t")) ? "\t" : ",";

  function parseSection(text) {
    const lines = text.split("\n").filter(l => l.trim());
    const headers = lines[0].split(delim).map(h => h.trim().replace(/^"|"$/g, "").toLowerCase());
    return lines.slice(1).map(line => {
      const vals = line.split(delim).map(v => v.trim().replace(/^"|"$/g, ""));
      const row = {};
      headers.forEach((h, i) => { row[h] = vals[i] || ""; });
      return row;
    }).filter(r => Object.values(r).some(v => v));
  }

  // Build city → address map
  const cityAddress = {};
  if (addressSection) {
    for (const row of parseSection(addressSection)) {
      const city = (row["city"] || "").trim().toLowerCase();
      const addr = row["delivery address"] || row["address"] || "";
      if (city && addr) cityAddress[city] = addr;
    }
  }

  // Parse retailers
  const retailers = [];
  for (const row of parseSection(retailerSection)) {
    const city = (row["city"] || "").trim();
    // Extract state if "City, ST" format
    let cityName = city, state = "";
    const cm = city.match(/^(.+?),\s*([A-Z]{2})$/);
    if (cm) { cityName = cm[1]; state = cm[2]; }

    const retailerName = row["retailer name"] || row["retailer"] || "";
    if (!retailerName) continue;

    retailers.push({
      city:            cityName,
      state:           state,
      density:         row["density"] || "",
      retailerType:    row["retailer type"] || row["type"] || "",
      retailerName:    retailerName,
      deliveryAddress: row["delivery address"] || cityAddress[cityName.toLowerCase()] || "",
      date:            row["date"] || new Date().toISOString().slice(0, 10),
    });
  }

  if (!retailers.length) throw new Error("No retailer rows found in the CSV.");
  return retailers;
}

function jobKey(platform, retailerName, city, basket, membership) {
  return `${platform}|${retailerName}|${city}|${basket}|${membership}`.toLowerCase();
}

// ── Import ────────────────────────────────────────────────────────────────────

async function loadAndImport(raw) {
  $("import-error").textContent = "";
  try {
    const retailers = parseSections(raw);
    const job = {
      retailers,
      platforms:    PLATFORMS,
      basketSizes:  BASKETS,
      memberships:  MEMBERSHIPS,
      collectionDate: retailers[0]?.date || new Date().toISOString().slice(0, 10),
    };
    const resp = await chrome.runtime.sendMessage({ type: "IMPORT_JOB", job });
    if (!resp?.ok) throw new Error("Failed to save job.");
    await refreshWeekly();
  } catch (err) {
    $("import-error").textContent = err.message;
  }
}

$("import-btn").addEventListener("click", async () => {
  const raw = $("import-paste").value.trim();
  if (!raw) { $("import-error").textContent = "Paste your CSV first."; return; }
  await loadAndImport(raw);
});

// Drag-and-drop / file pick
$("import-drop").addEventListener("click", () => $("import-file").click());
$("import-drop").addEventListener("dragover", e => { e.preventDefault(); $("import-drop").classList.add("drag-over"); });
$("import-drop").addEventListener("dragleave", () => $("import-drop").classList.remove("drag-over"));
$("import-drop").addEventListener("drop", async e => {
  e.preventDefault();
  $("import-drop").classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (!file) return;
  const text = await file.text();
  $("import-paste").value = text;
  await loadAndImport(text);
});
$("import-file").addEventListener("change", async () => {
  const file = $("import-file").files[0];
  if (!file) return;
  const text = await file.text();
  $("import-paste").value = text;
  await loadAndImport(text);
});

// ── Refresh / render ──────────────────────────────────────────────────────────

async function refreshWeekly() {
  const resp = await chrome.runtime.sendMessage({ type: "GET_JOB" });
  _job = resp?.job || null;
  _ctx = resp?.checkoutCtx || null;
  renderWeeklyJob();
}

function renderWeeklyJob() {
  if (!_job) {
    $("weekly-empty").style.display = "block";
    $("weekly-job").style.display   = "none";
    return;
  }
  $("weekly-empty").style.display = "none";
  $("weekly-job").style.display   = "block";

  // Progress
  const total = _job.retailers.length * PLATFORMS.length * BASKETS.length * MEMBERSHIPS.length;
  const done  = Object.keys(_job.captures || {}).length;
  $("weekly-date").textContent          = `Week of ${_job.collectionDate}`;
  $("weekly-progress-text").textContent = `${done} / ${total}`;
  $("progress-fill").style.width        = `${total ? (done / total * 100) : 0}%`;

  // Checkout banner
  if (_ctx && (Date.now() - _ctx.ts < 120_000)) { // stale after 2 min
    $("checkout-banner").style.display = "block";
    $("checkout-platform").textContent  = _ctx.platform;
    $("checkout-retailer").textContent  = _ctx.retailer ? ` / ${_ctx.retailer}` : "";
  } else {
    $("checkout-banner").style.display = "none";
    _ctx = null;
  }

  // Capture button enabled when: on checkout AND basket selected
  $("capture-btn").disabled = !(_ctx && _basket !== null);

  renderQueue();
}

function renderQueue() {
  const captures = _job.captures || {};
  const items = [];

  for (const r of _job.retailers) {
    for (const platform of PLATFORMS) {
      for (const basket of BASKETS) {
        for (const mem of MEMBERSHIPS) {
          const key  = jobKey(platform, r.retailerName, r.city, basket, mem);
          const done = !!captures[key];
          items.push({ r, platform, basket, mem, key, done });
        }
      }
    }
  }

  const pending = items.filter(i => !i.done);
  const next    = pending[0] || null;

  // Auto-suggest context for next item
  if (next && _basket === null) {
    setBasket(next.basket);
    setMembership(next.mem);
  }

  // Show last 2 done + next 8 pending
  const recent  = items.filter(i => i.done).slice(-2);
  const display = [...recent, ...pending.slice(0, 8)];

  $("queue-list").innerHTML = display.map(item => {
    const isNext = item === next;
    const icon   = item.done ? "✅" : (isNext ? "▶" : "○");
    const cls    = item.done ? "done" : (isNext ? "next" : "");
    const plat   = PLAT_SHORT[item.platform] || item.platform;
    return `<div class="qi ${cls}">
      <span class="qi-icon">${icon}</span>
      <span class="qi-main">${plat} · ${item.r.retailerName} · $${item.basket} · ${item.mem}</span>
      <span class="qi-city">${item.r.city}</span>
    </div>`;
  }).join("");
}

// ── Context chips ─────────────────────────────────────────────────────────────

function setBasket(size) {
  _basket = size;
  document.querySelectorAll("#basket-chips .chip").forEach(c => {
    c.classList.toggle("active", Number(c.dataset.size) === size);
  });
  $("capture-btn").disabled = !(_ctx && _basket !== null);
}

function setMembership(mem) {
  _membership = mem;
  document.querySelectorAll("#mem-chips .chip").forEach(c => {
    c.classList.toggle("active", c.dataset.mem === mem);
  });
}

document.querySelectorAll("#basket-chips .chip").forEach(c => {
  c.addEventListener("click", () => setBasket(Number(c.dataset.size)));
});
document.querySelectorAll("#mem-chips .chip").forEach(c => {
  c.addEventListener("click", () => setMembership(c.dataset.mem));
});

// ── Capture ───────────────────────────────────────────────────────────────────

function setCaptureStatus(msg, type) {
  const el = $("capture-status");
  el.textContent = msg;
  el.className   = type; // "ok" or "err" or ""
}

$("capture-btn").addEventListener("click", async () => {
  if (!_ctx || _basket === null || !_job) return;

  $("capture-btn").disabled  = true;
  $("capture-btn").textContent = "Analyzing…";
  setCaptureStatus("", "");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const pageData = await chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_DATA" });
    if (!pageData?.pageText) throw new Error("Could not read page. Try refreshing.");

    // Match retailer from job
    const matched = matchRetailer(_ctx.retailer, _job.retailers);
    if (!matched) throw new Error(`Retailer "${_ctx.retailer}" not found in job. Check the name matches.`);

    const settings = await chrome.storage.sync.get(["provider", "ollamaModel", "ollamaApiKey", "geminiApiKey"]);
    const prompt   = buildWeeklyPrompt(pageData.pageText, pageData.url, _ctx.platform, _basket, _membership);

    let result;
    const provider = settings.provider || "ollama";
    if (provider === "gemini") {
      result = await callGemini(settings.geminiApiKey, prompt);
    } else {
      result = await callOllama(settings.ollamaModel || "llama3.2", prompt, settings.ollamaApiKey);
    }

    const key  = jobKey(_ctx.platform, matched.retailerName, matched.city, _basket, _membership);
    const data = {
      ...result,
      platform:        _ctx.platform,
      retailerName:    matched.retailerName,
      city:            matched.city,
      state:           matched.state,
      density:         matched.density,
      retailerType:    matched.retailerType,
      deliveryAddress: matched.deliveryAddress,
      basketTarget:    _basket,
      membership:      _membership,
      capturedAt:      new Date().toISOString(),
    };

    await chrome.runtime.sendMessage({ type: "SAVE_WEEKLY_CAPTURE", key, data, tabId: tab.id });

    setCaptureStatus(`✅ Saved — ${_ctx.platform} / ${matched.retailerName} / $${_basket} / ${_membership}`, "ok");
    _ctx = null;

    // Advance to next item in queue
    await refreshWeekly();

  } catch (err) {
    setCaptureStatus(err.message, "err");
  } finally {
    $("capture-btn").disabled    = false;
    $("capture-btn").textContent = "📸 Capture Checkout";
  }
});

// ── Retailer matcher ──────────────────────────────────────────────────────────

function matchRetailer(detected, retailers) {
  if (!detected) return null;
  const d = detected.toLowerCase().trim();
  // Exact match first
  let found = retailers.find(r => r.retailerName.toLowerCase() === d);
  if (found) return found;
  // Partial match: job name contained in detected or vice versa
  found = retailers.find(r => {
    const n = r.retailerName.toLowerCase();
    return d.includes(n) || n.includes(d);
  });
  return found || null;
}

// ── AI prompt for weekly capture ──────────────────────────────────────────────

function buildWeeklyPrompt(pageText, url, platform, basketTarget, membership) {
  return `You are extracting delivery fee data from a ${platform} checkout page.
Basket target: $${basketTarget} | Membership status: ${membership}

Page URL: ${url}
Page content:
---
${pageText.slice(0, 10000)}
---

Return ONLY a valid JSON object — no markdown, no explanation:
{
  "store": "<retailer name>",
  "item_total": "<merchandise subtotal before fees, e.g. '48.50'>",
  "delivery_fee": "<delivery fee or 'Free' or '0'>",
  "service_fee": "<platform service fee>",
  "regulatory_fee": "<regulatory fee if shown, else null>",
  "long_distance_fee": "<long distance fee if shown, else null>",
  "small_order_fee": "<small order fee if shown, else null>",
  "taxes": "<total taxes>",
  "order_total": "<grand total>",
  "delivery_time_min": "<minimum delivery minutes as number, e.g. 25>",
  "delivery_time_max": "<maximum delivery minutes as number, e.g. 45>",
  "priority_delivery_time": "<priority option minutes if shown, else null>",
  "priority_delivery_fee": "<priority delivery extra fee if shown, else null>",
  "minimum_order": "<minimum order requirement if shown, else null>",
  "notes": "<any notable observations or null>"
}
Use null for anything not visible on the page. Return numbers as strings without $ signs.`;
}

// ── Export CSV ────────────────────────────────────────────────────────────────

$("export-btn").addEventListener("click", exportJobCSV);

function exportJobCSV() {
  if (!_job) return;

  const captures = _job.captures || {};

  const COLS = [
    "Platform", "Retailer", "Membership Status", "City", "State", "Density",
    "Retailer Type", "Delivery Address", "Basket Size Target ($)",
    "Date Collected", "Merch Order Size ($)", "Delivery Fee ($)",
    "Service Fee ($)", "Regulatory Fee ($)", "Long Distance Fee ($)",
    "Small Order Fee ($)", "Taxes ($)", "Total Cart ($)",
    "Min Delivery Time (min)", "Max Delivery Time (min)",
    "Priority Delivery Time (min)", "Priority Delivery Fee ($)",
    "Minimum Order ($)", "SF%", "Total Fee%",
    "DD-Mem-$", "DD-NonMem-$", "IC-Mem-$", "IC-NonMem-$",
    "DD-Mem-%", "DD-NonMem-%", "IC-Mem-%", "IC-NonMem-%",
    "UE-Mem-$", "UE-NonMem-$", "UE-Mem-%", "UE-NonMem-%",
    "Notes",
  ];

  // Build all rows
  const dataRows = [];
  for (const r of _job.retailers) {
    for (const platform of PLATFORMS) {
      for (const basket of BASKETS) {
        for (const mem of MEMBERSHIPS) {
          const key = jobKey(platform, r.retailerName, r.city, basket, mem);
          const c   = captures[key] || {};

          const merch    = parseFloat(c.item_total)        || 0;
          const delivery = parseFloat(c.delivery_fee)      || 0;
          const svc      = parseFloat(c.service_fee)       || 0;
          const reg      = parseFloat(c.regulatory_fee)    || 0;
          const ld       = parseFloat(c.long_distance_fee) || 0;
          const sof      = parseFloat(c.small_order_fee)   || 0;
          const taxes    = parseFloat(c.taxes)             || 0;
          const total    = parseFloat(c.order_total)       || 0;

          const sfPct      = merch ? ((svc / merch) * 100).toFixed(1) + "%" : "";
          const totalFees  = delivery + svc + reg + ld + sof;
          const totalFPct  = merch ? ((totalFees / merch) * 100).toFixed(1) + "%" : "";

          dataRows.push({
            platform, r, basket, mem, key,
            row: [
              platform, r.retailerName, mem, r.city, r.state, r.density,
              r.retailerType, r.deliveryAddress, basket,
              c.capturedAt ? c.capturedAt.slice(0, 10) : _job.collectionDate,
              c.item_total || "", c.delivery_fee || "", c.service_fee || "",
              c.regulatory_fee || "", c.long_distance_fee || "",
              c.small_order_fee || "", c.taxes || "", c.order_total || "",
              c.delivery_time_min || "", c.delivery_time_max || "",
              c.priority_delivery_time || "", c.priority_delivery_fee || "",
              c.minimum_order || "", sfPct, totalFPct,
              // comparison cols — filled below
              "", "", "", "", "", "", "", "", "", "", "", "",
              c.notes || "",
            ],
          });
        }
      }
    }
  }

  // Fill comparison columns (indices 25-36)
  // Group by (retailer, city, basket)
  const groups = {};
  for (const dr of dataRows) {
    const gk = `${dr.r.retailerName}|${dr.r.city}|${dr.basket}`;
    if (!groups[gk]) groups[gk] = {};
    groups[gk][`${dr.platform}|${dr.mem}`] = dr.row[17]; // order_total col
  }

  const COL_IDX = { // column indices in row array (0-based)
    "DD-Mem-$":     25, "DD-NonMem-$":  26, "IC-Mem-$":     27, "IC-NonMem-$":  28,
    "DD-Mem-%":     29, "DD-NonMem-%":  30, "IC-Mem-%":     31, "IC-NonMem-%":  32,
    "UE-Mem-$":     33, "UE-NonMem-$":  34, "UE-Mem-%":     35, "UE-NonMem-%":  36,
  };

  for (const dr of dataRows) {
    const gk      = `${dr.r.retailerName}|${dr.r.city}|${dr.basket}`;
    const g       = groups[gk] || {};
    const bTarget = dr.basket;

    function pctVsTarget(totalStr) {
      const t = parseFloat(totalStr);
      if (!t || !bTarget) return "";
      return ((t - bTarget) / bTarget * 100).toFixed(1) + "%";
    }

    dr.row[COL_IDX["DD-Mem-$"]]    = g["DoorDash|Member"]      || "";
    dr.row[COL_IDX["DD-NonMem-$"]] = g["DoorDash|Non-Member"]  || "";
    dr.row[COL_IDX["IC-Mem-$"]]    = g["Instacart|Member"]     || "";
    dr.row[COL_IDX["IC-NonMem-$"]] = g["Instacart|Non-Member"] || "";
    dr.row[COL_IDX["UE-Mem-$"]]    = g["Uber Eats|Member"]     || "";
    dr.row[COL_IDX["UE-NonMem-$"]] = g["Uber Eats|Non-Member"] || "";
    dr.row[COL_IDX["DD-Mem-%"]]    = pctVsTarget(dr.row[COL_IDX["DD-Mem-$"]]);
    dr.row[COL_IDX["DD-NonMem-%"]] = pctVsTarget(dr.row[COL_IDX["DD-NonMem-$"]]);
    dr.row[COL_IDX["IC-Mem-%"]]    = pctVsTarget(dr.row[COL_IDX["IC-Mem-$"]]);
    dr.row[COL_IDX["IC-NonMem-%"]] = pctVsTarget(dr.row[COL_IDX["IC-NonMem-$"]]);
    dr.row[COL_IDX["UE-Mem-%"]]    = pctVsTarget(dr.row[COL_IDX["UE-Mem-$"]]);
    dr.row[COL_IDX["UE-NonMem-%"]] = pctVsTarget(dr.row[COL_IDX["UE-NonMem-$"]]);
  }

  // Build CSV string
  const csvRows = [COLS, ...dataRows.map(dr => dr.row)];
  const csv = csvRows.map(row =>
    row.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")
  ).join("\n");

  const blob = new Blob([csv], { type: "text/csv" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `fee-data-${_job.collectionDate}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Re-import / clear ─────────────────────────────────────────────────────────

$("reimport-btn").addEventListener("click", () => {
  $("weekly-job").style.display   = "none";
  $("weekly-empty").style.display = "block";
  $("import-paste").value = "";
  _job = null;
});

$("clear-job-btn").addEventListener("click", async () => {
  if (!confirm("Clear all captured data for this week? This cannot be undone.")) return;
  await chrome.runtime.sendMessage({ type: "CLEAR_JOB" });
  _job = null;
  renderWeeklyJob();
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
