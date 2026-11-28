import { get, writable } from "svelte/store";
import { ensureDebug } from "./debug";
import { queryClient } from "./queryClient";
import { refreshScreen, stopScreenRefresh } from "./resourceVersions/refresh";
import { RESOURCE_REFRESH_DEBOUNCE_MS } from "./resourceVersions/config";
import { CONNECTION_PROBE_TIMEOUT_MS, UPDATE_RECOVERY_POLL_MS } from "./updates/config";
import { flushConnectionEvents, recordProbe, startConnectionLog, stopConnectionLog } from "./connectionLog";
import { retryLiveSocket, startLiveSocket, stopLiveSocket } from "./liveSocket";

// The server sends one contentless "something changed" frame per commit over the live socket. The
// browser probes versions for mounted resources and refetches changed bodies.
// A read with no registered revision is read again on each round. The signal stays contentless.
//
// The page is `connecting` until its socket is ready: that is a start, so no card shows. After
// that, a lost socket is `unreachable` at once and the card shows. When the operating system
// reports no network, it is `offline` at once instead, because the server is not at fault.
export type ConnectionStatus = "connecting" | "connected" | "unreachable" | "offline";

// Commits inside one fixed window collapse into one round of refetches. A working agent
// commits in bursts of dozens, and each round asks the server for everything on screen
// again. The first commit starts the deadline so a sustained stream cannot keep the visible
// screen stale. An own write starts the same refresh at once, without this delay.
export const INVALIDATE_DEBOUNCE_MS = RESOURCE_REFRESH_DEBOUNCE_MS;

export const connectionStatus = writable<ConnectionStatus>("connecting");

let flushTimer: number | null = null;
let probeTimer: number | null = null;
let probing = false;
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
  window.addEventListener("offline", onNetworkOffline);
  window.addEventListener("online", onNetworkOnline);
  connectionStatus.set(navigator.onLine === false ? "offline" : "connecting");
  startLiveSocket({
    ready: () => {
      flushConnectionEvents();
      clearRecovery();
      ensureDebug().sseOpens += 1;
      connectionStatus.set("connected");
      // A connection just opened, so anything that changed while it was down is
      // still unseen: reconcile by refetching what is on screen.
      if (document.visibilityState === "hidden") return;
      void reconcileResources().then(() => {
        ensureDebug().sseReconciliations += 1;
      });
    },
    change: () => {
      // A hidden tab has nothing on screen to bring up to date, and refetching everything
      // for it is the whole cost with none of the point. One round when it comes back
      // answers everything that landed while it was away, however much that was.
      if (document.visibilityState === "hidden") return;
      if (flushTimer === null) {
        flushTimer = window.setTimeout(flush, INVALIDATE_DEBOUNCE_MS);
      }
    },
    lost: socketLost,
    // The server answered, so Panels is reachable. The existing update prompt reads the status.
    updateNeeded: () => {
      connectionStatus.set("connected");
      void queryClient.refetchQueries({ queryKey: ["updates"] });
    },
  });
}

/** A lost socket shows the card at once. The page asks the server now, for the connection record. */
function socketLost(): void {
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

/** The same recovery path serves the timer and the owner's Try now action. */
export function retryChangeStream(): void {
  if (!started) return;
  void queryClient.refetchQueries({ queryKey: ["updates"] });
  retryLiveSocket();
}

export function stopChangeStream(): void {
  started = false;
  stopScreenRefresh();
  document.removeEventListener("visibilitychange", onVisibilityChange);
  stopConnectionLog();
  window.removeEventListener("offline", onNetworkOffline);
  window.removeEventListener("online", onNetworkOnline);
  clearRecovery();
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  stopLiveSocket();
  connectionStatus.set("connecting");
}
