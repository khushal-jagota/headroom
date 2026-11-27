import { afterEach, beforeEach, expect, it, vi } from "vitest";

// The log is the only record of why a stream ended, so its bound and its hand-off to the
// server are checked here. The server and Chromium parts are checked in the real desktop app.
const store = new Map<string, string>();
const requests: { body: { version: number; events: unknown[] }; headers: Record<string, string> }[] = [];
let answer = 200;

beforeEach(() => {
  vi.useFakeTimers();
  store.clear();
  requests.length = 0;
  answer = 200;
  vi.stubGlobal("window", { setInterval, clearInterval, addEventListener: () => {}, removeEventListener: () => {} });
  vi.stubGlobal("document", { visibilityState: "hidden", querySelector: () => null });
  vi.stubGlobal("navigator", { onLine: true });
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v) });
  vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    requests.push({ body: JSON.parse(init.body as string), headers: init.headers as Record<string, string> });
    return new Response(null, { status: answer });
  }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.resetModules(); });

const log = () => JSON.parse(store.get("panels.connection-events") ?? "[]") as Record<string, unknown>[];

it("records the cause with its context, keeps the newest 200, and sends once a connection returns", async () => {
  const { watchStream, startConnectionLog, flushConnectionEvents, CONNECTION_LOG_MAX_EVENTS, CONNECTION_LOG_SEND_GAP_MS } = await import("../src/lib/connectionLog");
  startConnectionLog();
  const watch = watchStream("/api/changes");
  watch.opened();
  await vi.advanceTimersByTimeAsync(4_000);
  watch.ended("error_event");
  watch.ended("error_event"); // one stream end is one event
  expect(log()).toHaveLength(1);
  expect(log()[0]).toMatchObject({ kind: "stream_ended", stream: "/api/changes", reason: "error_event", open_ms: 4_000, online: true, visible: false, drift_ms: 0, at: expect.any(Number) });

  for (let i = 0; i < CONNECTION_LOG_MAX_EVENTS + 5; i++) watchStream("/api/conversation/conversations/:conversation_id/tail").ended("error_event");
  expect(log()).toHaveLength(CONNECTION_LOG_MAX_EVENTS);

  // A refused send keeps the events. The next return, after the gap, sends them and clears them.
  answer = 503;
  flushConnectionEvents();
  await vi.advanceTimersByTimeAsync(0);
  expect(requests).toHaveLength(1);
  expect(requests[0].body.version).toBe(1);
  expect(requests[0].body.events).toHaveLength(50);
  expect(JSON.parse(requests[0].headers["X-Panels-Client"])).toMatchObject({ kind: "browser" });
  expect(log()).toHaveLength(CONNECTION_LOG_MAX_EVENTS);
  answer = 200;
  await vi.advanceTimersByTimeAsync(CONNECTION_LOG_SEND_GAP_MS);
  flushConnectionEvents();
  await vi.advanceTimersByTimeAsync(0);
  expect(log()).toHaveLength(CONNECTION_LOG_MAX_EVENTS - 50);
});
