import { ensureDebug } from "./debug";
import {
  invalidateCatalogueResources,
  keysForEvent,
  type CatalogueResourceIdentity,
  type PlannerEvent
} from "./resourceCatalogue";

type EventBatch = {
  events: PlannerEvent[];
  cursor: number;
};

type Options = {
  debounceMs?: number;
};

let cursor = 0;
let socket: WebSocket | null = null;
let retryMs = 500;
let flushTimer: number | null = null;
let started = false;
let stopped = false;
let pendingKeys = new Set<CatalogueResourceIdentity>();
let debounceMs = 250;

function wsUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss://" : "ws://";
  return `${scheme}${window.location.host}/api/events?since=${cursor}`;
}

function flush(): void {
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  const keys = Array.from(pendingKeys);
  pendingKeys = new Set();
  ensureDebug().flushes += 1;
  if (keys.length) {
    invalidateCatalogueResources(keys, "event stream");
  }
  console.debug(`[planner] flush ${ensureDebug().flushes}`);
}

function schedule(keys: readonly CatalogueResourceIdentity[]): void {
  for (const key of keys) pendingKeys.add(key);
  if (flushTimer !== null) window.clearTimeout(flushTimer);
  flushTimer = window.setTimeout(flush, debounceMs);
}

function connect(): void {
  if (stopped) return;
  socket = new WebSocket(wsUrl());
  socket.onopen = () => {
    retryMs = 500;
    ensureDebug().wsOpens += 1;
    console.debug(`[planner] ws open since=${cursor}`);
  };
  socket.onmessage = (event) => {
    let msg: EventBatch;
    try {
      msg = JSON.parse(event.data) as EventBatch;
    } catch {
      console.debug("[planner] ws bad json");
      return;
    }
    if (typeof msg.cursor === "number") {
      cursor = msg.cursor;
      ensureDebug().cursor = cursor;
    }
    const events = Array.isArray(msg.events) ? msg.events : [];
    ensureDebug().events += events.length;
    const keys: CatalogueResourceIdentity[] = [];
    for (const plannerEvent of events) {
      try {
        keys.push(...keysForEvent(plannerEvent));
      } catch (err) {
        console.debug("[planner] event mapping failed", err);
      }
    }
    schedule(keys);
  };
  socket.onclose = () => {
    socket = null;
    if (stopped) return;
    window.setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 10_000);
  };
}

export function startEventStream(options: Options = {}): void {
  if (started) return;
  started = true;
  stopped = false;
  debounceMs = options.debounceMs || 250;
  connect();
}

export function stopEventStream(): void {
  stopped = true;
  started = false;
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  pendingKeys = new Set<CatalogueResourceIdentity>();
  socket?.close();
  socket = null;
}

export function __eventCursor(): number {
  return cursor;
}
