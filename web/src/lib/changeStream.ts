import { get, writable } from "svelte/store";
import { ensureDebug } from "./debug";
import { queryClient } from "./queryClient";
import { refreshScreen, stopScreenRefresh } from "./resourceVersions/refresh";
import { RESOURCE_REFRESH_DEBOUNCE_MS } from "./resourceVersions/config";
import {
  CONNECTION_PROBE_TIMEOUT_MS, STREAM_SILENCE_MS, UPDATE_RECOVERY_POLL_MS
} from "./updates/config";
import {
  flushConnectionEvents, recordProbe, startConnectionLog, stopConnectionLog, watchStream
} from "./connectionLog";

// The server sends one contentless "something changed" frame per commit. The
// browser probes versions for mounted resources and refetches changed bodies.
// A read with no registered revision is read again on each round. The signal stays contentless.
//
// A lost stream is `unreachable` at once and the card shows. When the operating system reports
// no network, it is `offline` at once instead, because the server is not at fault.
export type ConnectionStatus = "connected" | "unreachable" | "offline";

// Commits inside one fixed window collapse into one round of refetches. A working agent
// commits in bursts of dozens, and each round asks the server for everything on screen
// again. The first commit starts the deadline so a sustained stream cannot keep the visible
// screen stale. An own write starts the same refresh at once, without this delay.
export const INVALIDATE_DEBOUNCE_MS = RESOURCE_REFRESH_DEBOUNCE_MS;

export const connectionStatus = writable<ConnectionStatus>("unreachable");

let stream: EventSource | null = null;
let flushTimer: number | null = null;
let reconnectTimer: number | null = null;
let probeTimer: number | null = null;
let probing = false;
let silenceTimer: number | null = null;
let started = false;
/** The record of the open stream, so that silence can name its cause. */
let streamWatch: ReturnType<typeof watchStream> | null = null;

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
  window.addEventListener("offline", onNetworkOffline);
  window.addEventListener("online", onNetworkOnline);
  connectionStatus.set(navigator.onLine === false ? "offline" : "unreachable");
  openStream();
}

function openStream(): void {
  if (!started) return;
  const source = new EventSource("/api/changes");
  stream = source;
  const thisWatch = streamWatch = watchStream("/api/changes");
  source.onopen = () => {
    if (stream !== source) return;
    thisWatch.opened();
    flushConnectionEvents();
    clearReconnect();
    clearRecovery();
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
    watchSilence(source);
    // A hidden tab has nothing on screen to bring up to date, and refetching everything
    // for it is the whole cost with none of the point. One round when it comes back
    // answers everything that landed while it was away, however much that was.
    if (document.visibilityState === "hidden") return;
    if (flushTimer === null) {
      flushTimer = window.setTimeout(flush, INVALIDATE_DEBOUNCE_MS);
    }
  };
  // The server's own heartbeat, a named frame because the page cannot see comment lines. It
  // starts the silence watch: an older server sends none, and a stream with no heartbeat to
  // expect is never judged silent.
  source.addEventListener("heartbeat", () => {
    if (stream !== source) return;
    watchSilence(source);
  });
  source.onerror = () => {
    if (stream !== source) return;
    thisWatch.ended("error_event");
    streamLost();
    // HTTP 503 can permanently close EventSource. Native retry cannot reopen it.
    if (source.readyState === 2 && reconnectTimer === null) {
      reconnectTimer = window.setTimeout(retryChangeStream, UPDATE_RECOVERY_POLL_MS);
    }
  };
}

/** Each frame moves the silence deadline. A stream that goes quiet is closed and reopened. */
function watchSilence(source: EventSource): void {
  if (silenceTimer !== null) window.clearTimeout(silenceTimer);
  silenceTimer = window.setTimeout(() => {
    silenceTimer = null;
    if (stream !== source) return;
    streamWatch?.ended("silence");
    source.close();
    streamLost();
    retryChangeStream();
  }, STREAM_SILENCE_MS);
}

/** A lost stream shows the card at once. The page asks the server now, for the connection record. */
function streamLost(): void {
  if (silenceTimer !== null) window.clearTimeout(silenceTimer);
  silenceTimer = null;
  connectionStatus.set(navigator.onLine === false ? "offline" : "unreachable");
  if (navigator.onLine === false) {
    clearRecovery();
    return;
  }
  probeServer();
}

/** Ask the server now and every few seconds, until the stream is live again. */
function probeServer(): void {
  if (probing || probeTimer !== null || !recovering()) return;
  probing = true;
  const sent = Date.now();
  fetch("/api/meta", { signal: AbortSignal.timeout(CONNECTION_PROBE_TIMEOUT_MS), cache: "no-store" })
    .then(response => recordProbe(response.ok, Date.now() - sent),
      () => recordProbe(false, Date.now() - sent))
    .finally(() => {
      probing = false;
      if (!recovering() || probeTimer !== null) return;
      probeTimer = window.setTimeout(() => { probeTimer = null; probeServer(); }, UPDATE_RECOVERY_POLL_MS);
    });
}

/** Recovery runs while the stream is down and the network is there. */
function recovering(): boolean {
  return started && get(connectionStatus) === "unreachable";
}

function clearRecovery(): void {
  if (probeTimer !== null) window.clearTimeout(probeTimer);
  probeTimer = null;
}

/** The operating system's own word that the network went or came back. */
function onNetworkOffline(): void {
  clearRecovery();
  connectionStatus.set("offline");
}

function onNetworkOnline(): void {
  if (get(connectionStatus) !== "offline") return;
  connectionStatus.set("unreachable");
  probeServer();
  retryChangeStream();
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
  void queryClient.refetchQueries({ queryKey: ["updates"] });
  openStream();
}

export function stopChangeStream(): void {
  started = false;
  clearReconnect();
  stopScreenRefresh();
  document.removeEventListener("visibilitychange", onVisibilityChange);
  stopConnectionLog();
  window.removeEventListener("offline", onNetworkOffline);
  window.removeEventListener("online", onNetworkOnline);
  clearRecovery();
  if (silenceTimer !== null) window.clearTimeout(silenceTimer);
  silenceTimer = null;
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  stream?.close();
  stream = null;
  connectionStatus.set("unreachable");
}
