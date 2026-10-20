import { writable } from "svelte/store";
import { ensureDebug } from "./debug";
import { queryClient } from "./queryClient";
import { createResourceCoordinator } from "./resourceVersions/coordinator";
import { RESOURCE_REFRESH_DEBOUNCE_MS } from "./resourceVersions/config";

// The server sends one contentless "something changed" frame per commit. The
// browser probes versions for mounted resources and refetches changed bodies.
// Unregistered reads retain explicit invalidation. The signal stays contentless.
export type ConnectionStatus = "connected" | "reconnecting";

// Commits inside one fixed window collapse into one round of refetches. A working agent
// commits in bursts of dozens, and each round asks the server for everything on screen
// again. The first commit starts the deadline so a sustained stream cannot keep the visible
// screen stale. Confirmed browser actions can refresh immediately without this delay.
export const INVALIDATE_DEBOUNCE_MS = RESOURCE_REFRESH_DEBOUNCE_MS;
const resources = createResourceCoordinator(queryClient);

export const connectionStatus = writable<ConnectionStatus>("reconnecting");

let stream: EventSource | null = null;
let flushTimer: number | null = null;
/** Something was committed while nobody was looking at this tab. */
let changedWhileHidden = false;

function reconcileResources(): Promise<void> {
  ensureDebug().flushes += 1;
  return resources.refresh();
}

function flush(): void {
  flushTimer = null;
  if (document.visibilityState === "hidden") { changedWhileHidden = true; return; }
  void reconcileResources();
}

/** Coming back to the tab is when what happened while it was away is worth fetching. */
function onVisibilityChange(): void {
  if (document.visibilityState !== "visible" || !changedWhileHidden) return;
  changedWhileHidden = false;
  void reconcileResources();
}

export function startChangeStream(): void {
  if (stream) return;
  document.addEventListener("visibilitychange", onVisibilityChange);
  const source = new EventSource("/api/changes");
  stream = source;
  source.onopen = () => {
    if (stream !== source) return;
    ensureDebug().sseOpens += 1;
    connectionStatus.set("connected");
    // A connection just opened, so anything that changed while it was down is
    // still unseen: reconcile by refetching what is on screen.
    if (document.visibilityState === "hidden") { changedWhileHidden = true; return; }
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
    if (document.visibilityState === "hidden") {
      changedWhileHidden = true;
      return;
    }
    if (flushTimer === null) {
      flushTimer = window.setTimeout(flush, INVALIDATE_DEBOUNCE_MS);
    }
  };
  source.onerror = () => {
    if (stream !== source) return;
    // EventSource reconnects on its own; the pill just says so.
    connectionStatus.set("reconnecting");
  };
}

export function stopChangeStream(): void {
  resources.stop();
  document.removeEventListener("visibilitychange", onVisibilityChange);
  changedWhileHidden = false;
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  stream?.close();
  stream = null;
  connectionStatus.set("reconnecting");
}
