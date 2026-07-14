import type { ChatImageUploadResponse, StartChatTurnBody } from "./types";

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

export async function startChatTurn(
  entityId: string,
  body: StartChatTurnBody
): Promise<unknown> {
  return fetchJson(`/api/chat/${encodeURIComponent(entityId)}/turns`, {
    method: "POST",
    body
  });
}

export async function continueChatTurn(entityId: string, turnId: string): Promise<unknown> {
  return fetchJson(
    `/api/chat/${encodeURIComponent(entityId)}/turns/${encodeURIComponent(turnId)}/continue`,
    { method: "POST" }
  );
}

export async function uploadChatImage(
  entityId: string,
  file: File
): Promise<ChatImageUploadResponse> {
  let response: Response;
  try {
    response = await fetch(`/api/chat/${encodeURIComponent(entityId)}/images`, {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name)
      },
      body: file
    });
  } catch {
    throw new PlannerFetchError("network error", "network");
  }

  const raw = await response.text();
  let parsed: unknown = null;
  try {
    parsed = raw === "" ? {} : JSON.parse(raw);
  } catch {
    if (response.ok) {
      throw new PlannerFetchError("invalid JSON in response", "bad_json", {
        status: response.status
      });
    }
  }
  if (response.ok) return parsed as ChatImageUploadResponse;
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

export async function pauseChatTurn(entityId: string): Promise<unknown> {
  return fetchJson(`/api/chat/${encodeURIComponent(entityId)}/pause`, {
    method: "POST"
  });
}
