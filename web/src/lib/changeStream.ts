import { get, writable } from "svelte/store";
import { ensureDebug } from "./debug";
import { queryClient } from "./queryClient";
import { refreshScreen, stopScreenRefresh } from "./resourceVersions/refresh";
import { RESOURCE_REFRESH_DEBOUNCE_MS } from "./resourceVersions/config";
import {
  CONNECTION_GRACE_MS, CONNECTION_PROBE_TIMEOUT_MS, STREAM_SILENCE_MS, UPDATE_RECOVERY_POLL_MS
} from "./updates/config";

// The server sends one contentless "something changed" frame per commit. The
// browser probes versions for mounted resources and refetches changed bodies.
// A read with no registered revision is read again on each round. The signal stays contentless.
//
// A lost stream is `recovering` first: nothing dims and no card shows while the page probes the
// server and the stream reopens. Only a grace time with no answer makes it `unreachable`. When
// the operating system reports no network, it is `offline` at once, because the server is not at fault.
export type ConnectionStatus = "connected" | "recovering" | "unreachable" | "offline";

// Commits inside one fixed window collapse into one round of refetches. A working agent
// commits in bursts of dozens, and each round asks the server for everything on screen
// again. The first commit starts the deadline so a sustained stream cannot keep the visible
// screen stale. An own write starts the same refresh at once, without this delay.
export const INVALIDATE_DEBOUNCE_MS = RESOURCE_REFRESH_DEBOUNCE_MS;

export const connectionStatus = writable<ConnectionStatus>("recovering");

let stream: EventSource | null = null;
let flushTimer: number | null = null;
let reconnectTimer: number | null = null;
let graceTimer: number | null = null;
let probeTimer: number | null = null;
let probing = false;
let silenceTimer: number | null = null;
let started = false;
/** The server answered a probe since the stream was lost. */
let answered = false;

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
  window.addEventListener("offline", onNetworkOffline);
  window.addEventListener("online", onNetworkOnline);
  // The first open is a recovery too: a server that never answers still ends in the card.
  connectionStatus.set(navigator.onLine === false ? "offline" : "recovering");
  if (navigator.onLine !== false) armGrace();
  openStream();
}

function openStream(): void {
  if (!started) return;
  const source = new EventSource("/api/changes");
  stream = source;
  source.onopen = () => {
    if (stream !== source) return;
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
    source.close();
    streamLost();
    retryChangeStream();
  }, STREAM_SILENCE_MS);
}

/**
 * A stream error is not yet news. The page keeps its data and its screen, asks the server at once,
 * and shows the unreachable card only if nothing answers within the grace time.
 */
function streamLost(): void {
  if (silenceTimer !== null) window.clearTimeout(silenceTimer);
  silenceTimer = null;
  if (navigator.onLine === false) {
    clearRecovery();
    connectionStatus.set("offline");
    return;
  }
  const status = get(connectionStatus);
  if (status === "connected" || status === "offline") {
    connectionStatus.set("recovering");
    answered = false;
    armGrace();
  }
  probeServer();
}

function armGrace(): void {
  if (graceTimer !== null) window.clearTimeout(graceTimer);
  graceTimer = window.setTimeout(() => {
    graceTimer = null;
    if (get(connectionStatus) !== "recovering") return;
    // The server answered, so the stream is on its way back. Give it one more grace time.
    if (answered) {
      answered = false;
      armGrace();
      return;
    }
    connectionStatus.set("unreachable");
  }, CONNECTION_GRACE_MS);
}

/** Ask the server now and every few seconds, until the stream is live again. */
function probeServer(): void {
  if (probing || probeTimer !== null || !recovering()) return;
  probing = true;
  fetch("/api/meta", { signal: AbortSignal.timeout(CONNECTION_PROBE_TIMEOUT_MS), cache: "no-store" })
    .then(response => { if (response.ok) answered = true; }, () => {})
    .finally(() => {
      probing = false;
      if (!recovering() || probeTimer !== null) return;
      probeTimer = window.setTimeout(() => { probeTimer = null; probeServer(); }, UPDATE_RECOVERY_POLL_MS);
    });
}

/** Recovery runs while the stream is down and the network is there. */
function recovering(): boolean {
  const status = get(connectionStatus);
  return started && (status === "recovering" || status === "unreachable");
}

function clearRecovery(): void {
  if (graceTimer !== null) window.clearTimeout(graceTimer);
  if (probeTimer !== null) window.clearTimeout(probeTimer);
  graceTimer = null;
  probeTimer = null;
  answered = false;
}

/** The operating system's own word that the network went or came back. */
function onNetworkOffline(): void {
  clearRecovery();
  connectionStatus.set("offline");
}

function onNetworkOnline(): void {
  if (get(connectionStatus) !== "offline") return;
  connectionStatus.set("recovering");
  answered = false;
  armGrace();
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
  connectionStatus.set("recovering");
}
