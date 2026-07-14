import { ensureDebug } from "./debug";
import {
  invalidateCatalogueResources,
  keysForEvent,
  reconcileSubscribedCatalogueResources,
  type CatalogueResourceIdentity,
  type PlannerEvent
} from "./resourceCatalogue";
import { writable } from "svelte/store";

type EventBatch = {
  events: PlannerEvent[];
  cursor: number;
};

export type ConnectionStatus = "connected" | "reconnecting" | "offline";

type Options = {
  debounceMs?: number;
  heartbeatMs?: number;
};

const INITIAL_RETRY_MS = 500;
const MAX_RETRY_MS = 10_000;
const DEFAULT_HEARTBEAT_MS = 15_000;

export const connectionStatus = writable<ConnectionStatus>("reconnecting");

let cursor = 0;
let socket: WebSocket | null = null;
let retryMs = INITIAL_RETRY_MS;
let flushTimer: number | null = null;
let reconnectTimer: number | null = null;
let missedHeartbeatTimer: number | null = null;
let offlineTimer: number | null = null;
let started = false;
let stopped = false;
let confirmedHealthy = false;
let reconcileOnNextHealthy = false;
let pendingKeys = new Set<CatalogueResourceIdentity>();
let debounceMs = 250;
let heartbeatMs = DEFAULT_HEARTBEAT_MS;
let currentStatus: ConnectionStatus = "reconnecting";

function setConnectionStatus(status: ConnectionStatus): void {
  currentStatus = status;
  connectionStatus.set(status);
}

function wsUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss://" : "ws://";
  return `${scheme}${window.location.host}/api/events?since=${cursor}`;
}

function clearTimer(timer: number | null): null {
  if (timer !== null) window.clearTimeout(timer);
  return null;
}

function clearLivenessTimers(): void {
  missedHeartbeatTimer = clearTimer(missedHeartbeatTimer);
  offlineTimer = clearTimer(offlineTimer);
}

function startOfflineTimer(delayMs = heartbeatMs * 3): void {
  offlineTimer = clearTimer(offlineTimer);
  offlineTimer = window.setTimeout(() => {
    if (!stopped && currentStatus !== "connected") {
      setConnectionStatus("offline");
      socket?.close();
      scheduleReconnect();
    }
  }, delayMs);
}

function startMissedHeartbeatTimer(): void {
  missedHeartbeatTimer = clearTimer(missedHeartbeatTimer);
  missedHeartbeatTimer = window.setTimeout(() => {
    markConnectionLost(heartbeatMs);
    socket?.close();
  }, heartbeatMs * 2);
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

function scheduleReconnect(): void {
  if (stopped || reconnectTimer !== null) return;
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, retryMs);
  retryMs = Math.min(retryMs * 2, MAX_RETRY_MS);
}

function markConnectionLost(offlineDelayMs = heartbeatMs * 3): void {
  if (stopped) return;
  missedHeartbeatTimer = clearTimer(missedHeartbeatTimer);
  if (confirmedHealthy) reconcileOnNextHealthy = true;
  if (currentStatus !== "offline") setConnectionStatus("reconnecting");
  if (offlineTimer === null) {
    startOfflineTimer(offlineDelayMs);
  }
  scheduleReconnect();
}

function confirmLiveness(): void {
  const shouldReconcile = reconcileOnNextHealthy;
  confirmedHealthy = true;
  reconcileOnNextHealthy = false;
  retryMs = INITIAL_RETRY_MS;
  reconnectTimer = clearTimer(reconnectTimer);
  clearLivenessTimers();
  setConnectionStatus("connected");
  startMissedHeartbeatTimer();
  if (shouldReconcile) {
    void reconcileSubscribedCatalogueResources();
  }
}

function connect(): void {
  if (stopped) return;
  const activeSocket = new WebSocket(wsUrl());
  socket = activeSocket;
  activeSocket.onopen = () => {
    if (stopped || socket !== activeSocket) return;
    ensureDebug().wsOpens += 1;
    startMissedHeartbeatTimer();
    console.debug(`[planner] ws open since=${cursor}`);
  };
  activeSocket.onmessage = (event) => {
    if (stopped || socket !== activeSocket) return;
    let msg: EventBatch;
    try {
      msg = JSON.parse(event.data) as EventBatch;
    } catch {
      console.debug("[planner] ws bad json");
      return;
    }
    if (typeof msg.cursor !== "number" || !Array.isArray(msg.events)) {
      console.debug("[planner] ws bad frame");
      return;
    }
    confirmLiveness();
    const events = msg.events;
    if (events.length === 0) return;
    if (typeof msg.cursor === "number") {
      cursor = msg.cursor;
      ensureDebug().cursor = cursor;
    }
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
  activeSocket.onclose = () => {
    if (stopped || socket !== activeSocket) return;
    socket = null;
    markConnectionLost();
  };
}

export function startEventStream(options: Options = {}): void {
  if (started) return;
  started = true;
  stopped = false;
  debounceMs = options.debounceMs || 250;
  heartbeatMs = options.heartbeatMs || DEFAULT_HEARTBEAT_MS;
  setConnectionStatus("reconnecting");
  startOfflineTimer();
  connect();
}

export function stopEventStream(): void {
  stopped = true;
  started = false;
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  reconnectTimer = clearTimer(reconnectTimer);
  clearLivenessTimers();
  confirmedHealthy = false;
  reconcileOnNextHealthy = false;
  retryMs = INITIAL_RETRY_MS;
  pendingKeys = new Set<CatalogueResourceIdentity>();
  socket?.close();
  socket = null;
  setConnectionStatus("reconnecting");
}

export function __eventCursor(): number {
  return cursor;
}
