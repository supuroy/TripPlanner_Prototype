/**
 * Plan review models sometimes return JSON glued to more JSON, or put a JSON blob
 * inside ``assistant_markdown``. Extract traveler-facing markdown for the chat UI.
 */

function extractFirstJsonObjectSlice(s: string): string | null {
  const start = s.indexOf("{");
  if (start < 0) return null;
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = start; i < s.length; i++) {
    const c = s[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') {
      inStr = true;
      continue;
    }
    if (c === "{") depth += 1;
    else if (c === "}") {
      depth -= 1;
      if (depth === 0) return s.slice(start, i + 1);
    }
  }
  return null;
}

export function parseFirstJsonObject(text: string): Record<string, unknown> | null {
  const slice = extractFirstJsonObjectSlice(text.trim());
  if (!slice) return null;
  try {
    const v = JSON.parse(slice) as unknown;
    if (v && typeof v === "object" && !Array.isArray(v)) return v as Record<string, unknown>;
    return null;
  } catch {
    return null;
  }
}

/** Strip NULs models sometimes emit inside markdown. */
function sanitizeMd(s: string): string {
  return s.replace(/\u0000/g, "").trim();
}

/**
 * Turn ``review.assistant_markdown`` (possibly nested JSON) into plain markdown for the feed.
 */
export function planReviewMarkdownForUi(raw: string | undefined | null): string {
  let t = sanitizeMd(String(raw ?? ""));
  if (!t) return "";
  if (!t.startsWith("{")) return t;
  let obj = parseFirstJsonObject(t);
  if (!obj) return t;
  let md = typeof obj.assistant_markdown === "string" ? sanitizeMd(obj.assistant_markdown) : "";
  if (!md) return t;
  if (md.startsWith("{") && md.includes('"assistant_markdown"')) {
    const inner = parseFirstJsonObject(md);
    if (inner && typeof inner.assistant_markdown === "string") {
      const innerMd = sanitizeMd(inner.assistant_markdown as string);
      if (innerMd) md = innerMd;
    }
  }
  return md;
}
