import { clientIdentityHeaders, randomClientId } from "./clientIdentity";

// A small local log of why this page's live streams ended. It waits in local storage and
// goes to the server in a batch when the connection returns. The server stores it beside its
// own record of the stream, so a drop has the page's cause and the server's cause together.
// Recording never throws and never makes the screen wait.

export type StreamName = "changes" | "navigation_events" | "conversation_tail";
export type EndReason = "error_event" | "silence" | "probe_failure";

export type ConnectionEvent = {
  id: string;
  at: string;
  source: "web";
  kind: "stream_ended";
  stream: StreamName;
  reason: EndReason;
  open_ms?: number;
  online: boolean;
  visibility: "visible" | "hidden";
  timer_drift_ms?: number;
  probe_ms?: number;
};

export const CONNECTION_LOG_KEY = "panels.connection-events";
export const CONNECTION_LOG_MAX_EVENTS = 200;
const MAX_BATCH = 100;
const TICK_MS = 1_000;
/** The page sends at most one batch in this time. */
export const CONNECTION_LOG_SEND_GAP_MS = 10_000;
const SEND_LIMIT_MS = 10_000;

let tickTimer: number | null = null;
let lastTick = 0;
let largestDrift = 0;
let sending = false;
let lastSend = 0;

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
export function watchStream(stream: StreamName): { opened(): void; ended(reason: EndReason): void } {
  const created = Date.now();
  let openedAt: number | null = null;
  let done = false;
  return {
    opened: () => { openedAt = Date.now(); },
    ended: (reason) => {
      if (done) return;
      done = true;
      // The log must never break the stream handling that called it.
      try { recordStreamEnd(stream, reason, Date.now() - (openedAt ?? created)); } catch { /* best effort */ }
    },
  };
}

function recordStreamEnd(stream: StreamName, reason: EndReason, openMs: number, probeMs?: number): void {
  const drift = Math.max(0, Math.round(largestDrift));
  largestDrift = 0;
  const event: ConnectionEvent = {
    id: randomClientId(),
    at: new Date().toISOString(),
    source: "web",
    kind: "stream_ended",
    stream,
    reason,
    open_ms: Math.round(openMs),
    online: navigator.onLine,
    visibility: document.visibilityState === "hidden" ? "hidden" : "visible",
    ...(tickTimer !== null ? { timer_drift_ms: drift } : {}),
    ...(probeMs !== undefined ? { probe_ms: Math.round(probeMs) } : {}),
  };
  store([...stored(), event].slice(-CONNECTION_LOG_MAX_EVENTS));
}

/**
 * Send what waits. The change stream calls this when it opens, which is the connection
 * returning. Nothing waits for it. A failed send keeps the events for the next return.
 * This uses plain `fetch`, not `clientFetch`, so an update's write pause never holds it.
 */
export function flushConnectionEvents(): void {
  if (sending || navigator.onLine === false || Date.now() - lastSend < CONNECTION_LOG_SEND_GAP_MS) return;
  const batch = stored().slice(0, MAX_BATCH);
  if (batch.length === 0) return;
  sending = true;
  lastSend = Date.now();
  void (async () => {
    try {
      const response = await fetch("/api/client-events", {
        method: "POST",
        headers: clientIdentityHeaders({ "content-type": "application/json" }),
        body: JSON.stringify({ version: 1, events: batch }),
        signal: AbortSignal.timeout(SEND_LIMIT_MS),
      });
      // A 400 can never succeed, so its batch goes like a stored one.
      if (response.status !== 204 && response.status !== 400) return;
      const sent = new Set(batch.map((event) => event.id));
      store(stored().filter((event) => !sent.has(event.id)));
    } catch {
      // The next return of the connection tries again.
    } finally {
      sending = false;
    }
  })();
}
