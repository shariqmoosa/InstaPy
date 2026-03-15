// Content script — runs in the context of every page.
// Listens for a message from popup.js requesting page data,
// then returns the page text and metadata.

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.type !== "GET_PAGE_DATA") return;

  // Extract visible text — skip script/style nodes
  function extractText(node, parts) {
    if (!node) return;
    if (node.nodeType === Node.TEXT_NODE) {
      const t = node.textContent.trim();
      if (t) parts.push(t);
      return;
    }
    const tag = node.tagName && node.tagName.toLowerCase();
    if (tag === "script" || tag === "style" || tag === "noscript") return;
    for (const child of node.childNodes) extractText(child, parts);
  }

  const parts = [];
  extractText(document.body, parts);
  // Deduplicate adjacent identical lines and limit size
  const seen = new Set();
  const lines = [];
  for (const p of parts) {
    if (!seen.has(p)) { seen.add(p); lines.push(p); }
  }
  const pageText = lines.join("\n").slice(0, 12000);

  sendResponse({
    url: location.href,
    title: document.title,
    pageText,
  });

  return true; // keep message channel open for async
});
