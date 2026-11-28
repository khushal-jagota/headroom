import { clientIdentityHeaders } from "./clientIdentity";

// A small local log of why this page's live streams ended. It waits in local storage and
// goes to the server in a batch when the connection returns. The server stores it beside its
// own record of the stream, so a drop has the page's cause and the server's cause together.
// Recording never throws and never makes the screen wait.

// The event shape is version 1 of `POST /api/client-events`, owned by the server's design for
// the stream-close record. The route names a stream, and the server names the client by header.
export type StreamName = "/api/live";
export type EndReason = "close" | "silence" | "sequence_gap" | "probe_failure";

export type ConnectionEvent = {
  /** Epoch milliseconds on this client's clock. */
  at: number;
  kind: "stream_ended" | "probe_ok" | "probe_failed";
  stream?: StreamName;
  reason?: EndReason;
  open_ms?: number;
  /** The WebSocket close code the page saw, when it saw one. */
  close_code?: number;
  /** The sequence number of the last frame that arrived. */
  last_seq?: number;
  online: boolean;
  visible: boolean;
  /** The largest gap in a 1 s timer since the last event. A large value shows sleep. */
  drift_ms?: number;
  /** Round-trip time of the `/api/meta` probe, which a lost stream starts. */
  probe_ms?: number;
};

export const CONNECTION_LOG_KEY = "panels.connection-events";
export const CONNECTION_LOG_MAX_EVENTS = 200;
const MAX_BATCH = 50;
const TICK_MS = 1_000;
/** The page sends at most one batch in this time. The server allows 12 a minute. */
export const CONNECTION_LOG_SEND_GAP_MS = 10_000;
/** After a 429 the server asks for a minute. */
const TOO_MANY_BATCHES_WAIT_MS = 60_000;
const SEND_LIMIT_MS = 10_000;

let tickTimer: number | null = null;
let lastTick = 0;
let largestDrift = 0;
let sending = false;
let nextSendAt = 0;

function stored(): ConnectionEvent[] {
  try {
    const value = JSON.parse(localStorage.getItem(CONNECTION_LOG_KEY) ?? "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function store(events: ConnectionEvent[]): void {
  try {
    localStorage.setItem(CONNECTION_LOG_KEY, JSON.stringify(events));
  } catch {
    // Storage is full or off. The log is a best effort.
  }
}

/**
 * A gap in a 1 s timer shows that the page, or the Mac, was asleep or frozen. Start it with
 * the change stream, which runs for as long as the app is open.
 */
export function startConnectionLog(): void {
  if (tickTimer !== null) return;
  lastTick = Date.now();
  tickTimer = window.setInterval(() => {
    const now = Date.now();
    largestDrift = Math.max(largestDrift, now - lastTick - TICK_MS);
    lastTick = now;
  }, TICK_MS);
}

export function stopConnectionLog(): void {
  if (tickTimer !== null) window.clearInterval(tickTimer);
  tickTimer = null;
}

/** One watch for each stream the page opens. `ended` records its first call only. */
export function watchStream(stream: StreamName): { opened(): void; ended(reason: EndReason, detail?: StreamEndDetail): void } {
  const created = Date.now();
  let openedAt: number | null = null;
  let done = false;
  return {
    opened: () => { openedAt = Date.now(); },
    ended: (reason, detail) => {
      if (done) return;
      done = true;
      // The log must never break the stream handling that called it.
      try { recordStreamEnd(stream, reason, Date.now() - (openedAt ?? created), detail); } catch { /* best effort */ }
    },
  };
}

function record(event: Pick<ConnectionEvent, "kind" | "stream" | "reason" | "open_ms" | "probe_ms" | "close_code" | "last_seq">): void {
  const drift = Math.max(0, Math.round(largestDrift));
  largestDrift = 0;
  store([...stored(), {
    at: Date.now(),
    ...event,
    online: navigator.onLine,
    visible: document.visibilityState !== "hidden",
    ...(tickTimer !== null ? { drift_ms: drift } : {}),
  }].slice(-CONNECTION_LOG_MAX_EVENTS));
}

export type StreamEndDetail = { close_code?: number; last_seq?: number };

function recordStreamEnd(stream: StreamName, reason: EndReason, openMs: number, detail: StreamEndDetail = {}): void {
  record({ kind: "stream_ended", stream, reason, open_ms: Math.round(openMs), ...detail });
}

/** The probe a lost stream starts. Its round-trip time shows whether the server was slow or gone. */
export function recordProbe(ok: boolean, probeMs: number): void {
  try { record({ kind: ok ? "probe_ok" : "probe_failed", probe_ms: Math.round(probeMs) }); } catch { /* best effort */ }
}

/**
 * Send what waits. The change stream calls this when it opens, which is the connection
 * returning. Nothing waits for it. A failed send keeps the events for the next return.
 * This uses plain `fetch`, not `clientFetch`, so an update's write pause never holds it.
 */
export function flushConnectionEvents(): void {
  if (sending || navigator.onLine === false || Date.now() < nextSendAt) return;
  const batch = stored().slice(0, MAX_BATCH);
  if (batch.length === 0) return;
  sending = true;
  nextSendAt = Date.now() + CONNECTION_LOG_SEND_GAP_MS;
  void (async () => {
    try {
      const response = await fetch("/api/client-events", {
        method: "POST",
        headers: clientIdentityHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({ version: 1, events: batch }),
        signal: AbortSignal.timeout(SEND_LIMIT_MS),
      });
      // Any 2xx means stored. A refused shape (400, 413, 422) can never succeed, so its batch
      // goes like a stored one. A 429 or 503 keeps the batch.
      if (response.status === 429) nextSendAt = Date.now() + TOO_MANY_BATCHES_WAIT_MS;
      if (!response.ok && ![400, 413, 422].includes(response.status)) return;
      // Newer events may have joined the log during the send. The sent ones are the oldest.
      store(stored().slice(batch.length));
    } catch {
      // The next return of the connection tries again.
    } finally {
      sending = false;
    }
  })();
}
