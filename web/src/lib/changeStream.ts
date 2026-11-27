import { writable } from "svelte/store";
import { ensureDebug } from "./debug";
import { queryClient } from "./queryClient";
import { refreshScreen, stopScreenRefresh } from "./resourceVersions/refresh";
import { RESOURCE_REFRESH_DEBOUNCE_MS } from "./resourceVersions/config";
import { UPDATE_RECOVERY_POLL_MS } from "./updates/config";
import { flushConnectionEvents, startConnectionLog, stopConnectionLog, watchStream } from "./connectionLog";

// The server sends one contentless "something changed" frame per commit. The
// browser probes versions for mounted resources and refetches changed bodies.
// A read with no registered revision is read again on each round. The signal stays contentless.
export type ConnectionStatus = "connected" | "reconnecting";

// Commits inside one fixed window collapse into one round of refetches. A working agent
// commits in bursts of dozens, and each round asks the server for everything on screen
// again. The first commit starts the deadline so a sustained stream cannot keep the visible
// screen stale. An own write starts the same refresh at once, without this delay.
export const INVALIDATE_DEBOUNCE_MS = RESOURCE_REFRESH_DEBOUNCE_MS;

export const connectionStatus = writable<ConnectionStatus>("reconnecting");

let stream: EventSource | null = null;
let watch: ReturnType<typeof watchStream> | null = null;
let flushTimer: number | null = null;
let reconnectTimer: number | null = null;
let started = false;

function reconcileResources(): Promise<void> {
  ensureDebug().flushes += 1;
  return refreshScreen();
}

function flush(): void {
  flushTimer = null;
  if (document.visibilityState === "hidden") return;
  void reconcileResources();
}

/**
 * A hidden tab reads nothing. Its return is the one occasion that answers for all of the
 * time away, whether a frame arrived or the stream went silent. The revision check finds
 * what changed, so the cache library's own re-read of every query on focus is off.
 */
function onVisibilityChange(): void {
  if (document.visibilityState !== "visible") return;
  void reconcileResources();
}

export function startChangeStream(): void {
  if (started) return;
  started = true;
  document.addEventListener("visibilitychange", onVisibilityChange);
  startConnectionLog();
  openStream();
}

function openStream(): void {
  if (!started) return;
  const source = new EventSource("/api/changes");
  stream = source;
  const thisWatch = watch = watchStream("changes");
  source.onopen = () => {
    if (stream !== source) return;
    thisWatch.opened();
    flushConnectionEvents();
    clearReconnect();
    ensureDebug().sseOpens += 1;
    connectionStatus.set("connected");
    // A connection just opened, so anything that changed while it was down is
    // still unseen: reconcile by refetching what is on screen.
    if (document.visibilityState === "hidden") return;
    void reconcileResources().then(() => {
      if (stream !== source) return;
      ensureDebug().sseReconciliations += 1;
    });
  };
  source.onmessage = () => {
    if (stream !== source) return;
    // A hidden tab has nothing on screen to bring up to date, and refetching everything
    // for it is the whole cost with none of the point. One round when it comes back
    // answers everything that landed while it was away, however much that was.
    if (document.visibilityState === "hidden") return;
    if (flushTimer === null) {
      flushTimer = window.setTimeout(flush, INVALIDATE_DEBOUNCE_MS);
    }
  };
  source.onerror = () => {
    if (stream !== source) return;
    thisWatch.ended("error_event");
    connectionStatus.set("reconnecting");
    // HTTP 503 can permanently close EventSource. Native retry cannot reopen it.
    if (source.readyState === 2 && reconnectTimer === null) {
      reconnectTimer = window.setTimeout(retryChangeStream, UPDATE_RECOVERY_POLL_MS);
    }
  };
}

function clearReconnect(): void {
  if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
  reconnectTimer = null;
}

/** The same recovery path serves the timer and the owner's Try now action. */
export function retryChangeStream(): void {
  if (!started) return;
  clearReconnect();
  stream?.close();
  stream = null;
  connectionStatus.set("reconnecting");
  void queryClient.refetchQueries({ queryKey: ["updates"] });
  openStream();
}

export function stopChangeStream(): void {
  started = false;
  clearReconnect();
  stopScreenRefresh();
  document.removeEventListener("visibilitychange", onVisibilityChange);
  stopConnectionLog();
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  stream?.close();
  stream = null;
  connectionStatus.set("reconnecting");
}
