export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonObject = { [key: string]: JsonValue };

export type FetchOptions = {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
};

export class PlannerFetchError extends Error {
  code: string;
  detail?: unknown;
  status?: number;
  isPlannerError?: boolean;

  constructor(message: string, code: string, extra: Partial<PlannerFetchError> = {}) {
    super(message);
    this.name = "PlannerFetchError";
    this.code = code;
    Object.assign(this, extra);
  }
}

function makePlannerError(envelope: JsonObject, status: number): PlannerFetchError {
  return new PlannerFetchError(String(envelope.message || "request failed"), String(envelope.code), {
    detail: envelope.detail,
    status,
    isPlannerError: true
  });
}

export async function fetchJson<T = unknown>(path: string, options: FetchOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers || {}) };
  const init: RequestInit = {
    method: options.method || "GET",
    headers,
    signal: options.signal
  };
  if (options.body !== undefined && options.body !== null) {
    init.body = JSON.stringify(options.body);
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new PlannerFetchError("network error", "network");
  }

  const raw = await response.text();
  if (response.ok) {
    try {
      return (raw === "" ? {} : JSON.parse(raw)) as T;
    } catch {
      throw new PlannerFetchError("invalid JSON in response", "bad_json", {
        status: response.status
      });
    }
  }

  let parsed: unknown = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = null;
  }
  if (
    parsed &&
    typeof parsed === "object" &&
    "error" in parsed &&
    parsed.error &&
    typeof parsed.error === "object" &&
    "code" in parsed.error
  ) {
    throw makePlannerError(parsed.error as JsonObject, response.status);
  }
  throw new PlannerFetchError(`HTTP ${response.status}`, "http_error", {
    status: response.status
  });
}

export async function fetchText(path: string): Promise<string> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new PlannerFetchError(`HTTP ${response.status}`, "http_error", {
      status: response.status
    });
  }
  return response.text();
}

type SseEvent = {
  event: string;
  data: unknown;
};

function parseSseChunk(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  const parts = buffer.split(/\r?\n\r?\n/);
  const rest = parts.pop() || "";
  for (const part of parts) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of part.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) continue;
    const raw = dataLines.join("\n");
    try {
      events.push({ event, data: JSON.parse(raw) });
    } catch {
      events.push({ event, data: raw });
    }
  }
  return { events, rest };
}

export async function streamChat(
  entityId: string,
  body: { text: string; mode: "message" | "command" },
  handlers: {
    onEvent?: (event: string, data: unknown) => void;
    signal?: AbortSignal;
  } = {}
): Promise<void> {
  const response = await fetch(`/api/chat/${encodeURIComponent(entityId)}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: handlers.signal
  });
  if (!response.ok || !response.body) {
    throw new PlannerFetchError(`HTTP ${response.status}`, "http_error", {
      status: response.status
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseChunk(buffer);
    buffer = parsed.rest;
    for (const ev of parsed.events) {
      handlers.onEvent?.(ev.event, ev.data);
    }
  }
  if (buffer.trim()) {
    const parsed = parseSseChunk(buffer + "\n\n");
    for (const ev of parsed.events) {
      handlers.onEvent?.(ev.event, ev.data);
    }
  }
}

export async function startChatTurn(
  entityId: string,
  body: { text: string; mode: "message" | "command" }
): Promise<unknown> {
  return fetchJson(`/api/chat/${encodeURIComponent(entityId)}/turns`, {
    method: "POST",
    body
  });
}

export async function pauseChatTurn(entityId: string): Promise<unknown> {
  return fetchJson(`/api/chat/${encodeURIComponent(entityId)}/pause`, {
    method: "POST"
  });
}
