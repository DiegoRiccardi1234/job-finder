// Pure DOM/HTTP helpers shared across the app.

export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `API Error ${response.status}`);
  }
  return response.json();
}

export function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

export function truncate(value, max = 120) {
  const s = String(value || "");
  return s.length > max ? `${s.slice(0, max)}...` : s;
}

export function renderCoachMarkdown(raw) {
  // Input is HTML-escaped first. Apply minimal markdown: **bold**, *italic*,
  // `code`, and `-` bullet lists. Safe for untrusted content.
  const escaped = escapeHtml(raw || "");
  const lines = escaped.split(/\r?\n/);
  const html = [];
  let inList = false;
  for (const line of lines) {
    const bulletMatch = line.match(/^\s*-\s+(.*)$/);
    if (bulletMatch) {
      if (!inList) { html.push("<ul>"); inList = true; }
      html.push(`<li>${bulletMatch[1]}</li>`);
    } else {
      if (inList) { html.push("</ul>"); inList = false; }
      html.push(line);
    }
  }
  if (inList) html.push("</ul>");
  let joined = html.join("\n");
  joined = joined.replace(/\*\*([^*\n]+)\*\*/g, '<strong class="role-name">$1</strong>');
  joined = joined.replace(/(^|[\s(>])\*([^*\n]+)\*(?=[\s<.,;:!?)]|$)/g, '$1<em class="coach-hint">$2</em>');
  joined = joined.replace(/(^|[\s(>])_([^_\n]+)_(?=[\s<.,;:!?)]|$)/g, '$1<em class="coach-hint">$2</em>');
  joined = joined.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  joined = joined.replace(/\n(?!<)/g, "<br>");
  joined = joined.replace(/\n/g, "");
  return joined;
}

export function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(100%)";
    setTimeout(() => toast.remove(), 300);
  }, 3000);
  container.appendChild(toast);
}
