"use strict";

const SIZES = [25, 50, 75, 100];
const METRICS = {
  delivery_fee:  { label: "Delivery",  cls: "" },
  service_fee:   { label: "Service",   cls: "" },
  taxes:         { label: "Taxes",     cls: "" },
  delivery_time: { label: "Est. time", cls: "time" },
  order_total:   { label: "Total",     cls: "total" },
};

let activeMetric = "all";

// ── Render ────────────────────────────────────────────────────────────────────

function render(captures) {
  const wrap = document.getElementById("table-wrap");
  const stores = Object.keys(captures || {});

  if (stores.length === 0) {
    wrap.innerHTML = `<div class="empty-state">
      <h2>No data yet</h2>
      <p>Navigate to a store checkout, open the extension,<br/>
      select a basket size, and click <strong>Analyze Fees</strong>.<br/><br/>
      Results appear here automatically.</p>
    </div>`;
    document.getElementById("export-csv").disabled = true;
    return;
  }

  document.getElementById("export-csv").disabled = false;

  let html = `<table>
    <thead>
      <tr>
        <th class="store-col">Store</th>
        ${SIZES.map(s => `<th class="size-col">$${s}</th>`).join("")}
      </tr>
    </thead>
    <tbody>`;

  for (const store of stores) {
    html += `<tr>
      <td class="store-col">
        ${store}
      </td>`;
    for (const size of SIZES) {
      const d = captures[store]?.[size];
      if (!d) {
        html += `<td><div class="cell-empty">·</div></td>`;
        continue;
      }
      if (activeMetric === "all") {
        html += `<td><div class="cell-data">`;
        for (const [key, { label, cls }] of Object.entries(METRICS)) {
          const val = d[key];
          const isFree = val && /\b(free|£0|€0|\$0|0\.00)\b/i.test(val);
          html += `<div class="metric-line">
            <span class="metric-label">${label}</span>
            <span class="metric-value ${val ? (isFree ? "free" : cls) : "null"}">${val || "—"}</span>
          </div>`;
        }
        const ts = new Date(d.capturedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        html += `<div class="cell-ts">${ts}</div></div></td>`;
      } else {
        const val = d[activeMetric];
        const isFree = val && /\b(free|£0|€0|\$0|0\.00)\b/i.test(val);
        const cls = METRICS[activeMetric]?.cls || "";
        html += `<td><div class="cell-single ${val ? (isFree ? "free" : cls) : "null"}">${val || "—"}</div></td>`;
      }
    }
    html += `</tr>`;
  }

  html += `</tbody></table>`;
  wrap.innerHTML = html;
}

// ── Load & watch ──────────────────────────────────────────────────────────────

function load() {
  chrome.storage.local.get({ captures: {} }, ({ captures }) => render(captures));
}

chrome.storage.onChanged.addListener((changes) => {
  if (changes.captures) {
    flash();
    render(changes.captures.newValue || {});
  }
});

function flash() {
  const dot = document.getElementById("live-dot");
  dot.style.background = "#fff";
  setTimeout(() => { dot.style.background = "#4caf82"; }, 300);
}

// ── Metric filter ─────────────────────────────────────────────────────────────

document.querySelectorAll(".fchip").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".fchip").forEach(c => c.classList.remove("active"));
    btn.classList.add("active");
    activeMetric = btn.dataset.metric;
    chrome.storage.local.get({ captures: {} }, ({ captures }) => render(captures));
  });
});

// ── Export CSV ────────────────────────────────────────────────────────────────

document.getElementById("export-csv").addEventListener("click", () => {
  chrome.storage.local.get({ captures: {} }, ({ captures }) => {
    const stores = Object.keys(captures);
    if (!stores.length) return;

    const metricKeys = Object.keys(METRICS);
    // Header rows
    const rows = [];
    // Row 1: "Store", "$25 - Delivery", "$25 - Service", ..., "$50 - Delivery", ...
    const header = ["Store"];
    for (const size of SIZES) {
      for (const key of metricKeys) {
        header.push(`$${size} - ${METRICS[key].label}`);
      }
    }
    rows.push(header);

    for (const store of stores) {
      const row = [store];
      for (const size of SIZES) {
        const d = captures[store]?.[size];
        for (const key of metricKeys) {
          row.push(d?.[key] || "");
        }
      }
      rows.push(row);
    }

    const csv = rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "fee-comparison.csv";
    a.click(); URL.revokeObjectURL(url);
  });
});

// ── Clear ─────────────────────────────────────────────────────────────────────

document.getElementById("clear-all").addEventListener("click", () => {
  if (!confirm("Clear all captured data?")) return;
  chrome.storage.local.set({ captures: {} }, load);
});

// ── Init ──────────────────────────────────────────────────────────────────────

load();
