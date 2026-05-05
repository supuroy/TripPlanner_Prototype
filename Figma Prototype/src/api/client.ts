import { wanderlyActiveTraceHeaders } from "./traceContext";

function baseUrl(): string {
  const raw = import.meta.env.VITE_API_BASE as string | undefined;
  return (raw || "").replace(/\/$/, "");
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
  const url = `${baseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
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
    let message = (errText && errText.trim()) || res.statusText || `HTTP ${res.status}`;
    if (errText) {
      try {
        const parsed = JSON.parse(errText) as { error?: unknown };
        if (typeof parsed?.error === "string" && parsed.error.trim()) {
          message = parsed.error.trim();
        }
      } catch {
        /* keep message */
      }
    }
    throw new Error(message);
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
  const url = `${baseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
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
    let message = (text && text.trim()) || res.statusText || `HTTP ${res.status}`;
    if (text) {
      try {
        const body = JSON.parse(text) as { error?: unknown };
        if (typeof body?.error === "string" && body.error.trim()) {
          message = body.error.trim();
        }
      } catch {
        /* keep message as raw text */
      }
    }
    throw new Error(message);
  }
  return text ? (JSON.parse(text) as T) : ({} as T);
}
