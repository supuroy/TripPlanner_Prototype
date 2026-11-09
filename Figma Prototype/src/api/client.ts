import { wanderlyActiveTraceHeaders } from "./traceContext";

/**
 * Absolute URL for a Wanderly/API path. When `VITE_API_BASE` already ends with `/api`
 * (e.g. `http://127.0.0.1:8000/api`), callers must not also pass `/api/...` or the server
 * receives `/api/api/...` and returns 404 `{"error":"Not found"}` — no route matches.
 */
/** Turn HTTP error bodies into short user-facing messages (never dump SPA/HTML pages). */
export function formatHttpErrorMessage(
  status: number,
  statusText: string,
  bodyText: string,
): string {
  const raw = (bodyText && bodyText.trim()) || statusText || `HTTP ${status}`;
  const lower = raw.toLowerCase();
  if (lower.includes("<!doctype") || lower.includes("<html")) {
    if (status === 401 || lower.includes("sign_in")) {
      return "sign_in_required";
    }
    return `Request failed (HTTP ${status}). Try again in a moment.`;
  }
  try {
    const body = JSON.parse(raw) as { error?: unknown; reason?: unknown };
    if (typeof body?.error === "string" && body.error.trim()) {
      return body.error.trim();
    }
  } catch {
    /* keep raw */
  }
  if (raw.length > 240) {
    return `${raw.slice(0, 240)}…`;
  }
  return raw;
}

export function apiUrl(path: string): string {
  const raw = (import.meta.env.VITE_API_BASE as string | undefined)?.trim() || "";
  const base = raw.replace(/\/+$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  if (!base) return p;
  if (p.startsWith("/api/")) {
    const lb = base.toLowerCase();
    if (lb.endsWith("/api")) {
      return `${base}${p.slice("/api".length)}`;
    }
  }
  return `${base}${p}`;
}

/**
 * POST JSON and read `text/event-stream` frames as `data: {json}\\n\\n`.
 * Invokes onMessage for each JSON object; throws if stream ends without success (caller checks).
 */
export async function apiPostSseJson(
  path: string,
  body: Record<string, unknown>,
  onMessage: (msg: Record<string, unknown>) => void,
  options?: { signal?: AbortSignal; headers?: Record<string, string> },
): Promise<void> {
  const url = apiUrl(path);
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...wanderlyActiveTraceHeaders(),
      ...(options?.headers || {}),
    },
    body: JSON.stringify(body),
    signal: options?.signal,
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(formatHttpErrorMessage(res.status, res.statusText, errText));
  }
  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body for event stream");
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    for (;;) {
      const idx = buf.indexOf("\n\n");
      if (idx < 0) break;
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const ln of block.split("\n")) {
        if (ln.startsWith("data:")) {
          const raw = ln.slice(5).trim();
          if (!raw) continue;
          const obj = JSON.parse(raw) as Record<string, unknown>;
          onMessage(obj);
        }
      }
    }
  }
  const tail = buf.trim();
  if (tail) {
    for (const ln of tail.split("\n")) {
      if (ln.startsWith("data:")) {
        const raw = ln.slice(5).trim();
        if (raw) onMessage(JSON.parse(raw) as Record<string, unknown>);
      }
    }
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = apiUrl(path);
  const headers: HeadersInit = {
    Accept: "application/json",
    ...wanderlyActiveTraceHeaders(),
    ...(init?.headers || {}),
  };
  if (init?.body && !(headers as Record<string, string>)["Content-Type"]) {
    (headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(url, { ...init, headers });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(formatHttpErrorMessage(res.status, res.statusText, text));
  }
  return text ? (JSON.parse(text) as T) : ({} as T);
}

/** POST JSON and return raw bytes (e.g. PDF download). */
export async function apiFetchBlob(
  path: string,
  init?: RequestInit,
): Promise<{ blob: Blob; filename: string }> {
  const url = apiUrl(path);
  const headers: HeadersInit = {
    Accept: "application/pdf,*/*",
    ...wanderlyActiveTraceHeaders(),
    ...(init?.headers || {}),
  };
  if (init?.body && !(headers as Record<string, string>)["Content-Type"]) {
    (headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(formatHttpErrorMessage(res.status, res.statusText, errText));
  }
  const blob = await res.blob();
  const ct = (res.headers.get("Content-Type") || "").toLowerCase();
  if (ct.includes("application/json")) {
    const errText = await blob.text();
    let message = errText.trim() || "Export failed";
    try {
      const body = JSON.parse(errText) as { error?: unknown };
      if (typeof body?.error === "string" && body.error.trim()) message = body.error.trim();
    } catch {
      /* keep message */
    }
    throw new Error(message);
  }
  let filename = "itinerary.pdf";
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*?=(?:UTF-8''|")?([^";\n]+)/i.exec(cd);
  if (m?.[1]) {
    try {
      filename = decodeURIComponent(m[1].trim().replace(/^"|"$/g, ""));
    } catch {
      filename = m[1].trim().replace(/^"|"$/g, "");
    }
  }
  return { blob, filename };
}
