import { writable } from "svelte/store";
import { ensureDebug } from "./debug";
import { queryClient } from "./queryClient";

// The server sends one contentless "something changed" frame per commit. The
// browser answers by marking every cached read stale; only the queries a screen
// is actually using refetch. Nothing about what changed travels over the wire.
export type ConnectionStatus = "connected" | "reconnecting";

// A burst of commits collapses into one round of refetches. A working agent commits in
// bursts of dozens, and each round asks the server for everything on screen again, so the
// window is wide enough to swallow a whole burst: a board-level fact — a status, a count, a
// card's place — landing a second and a half later is not something a reader can see.
export const INVALIDATE_DEBOUNCE_MS = 1_500;

export const connectionStatus = writable<ConnectionStatus>("reconnecting");

let stream: EventSource | null = null;
let flushTimer: number | null = null;
/** Something was committed while nobody was looking at this tab. */
let changedWhileHidden = false;

function invalidateEverything(): Promise<void> {
  ensureDebug().flushes += 1;
  return queryClient.invalidateQueries();
}

function flush(): void {
  flushTimer = null;
  void invalidateEverything();
}

/** Coming back to the tab is when what happened while it was away is worth fetching. */
function onVisibilityChange(): void {
  if (document.visibilityState !== "visible" || !changedWhileHidden) return;
  changedWhileHidden = false;
  void invalidateEverything();
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
    void invalidateEverything().then(() => {
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
    if (flushTimer !== null) window.clearTimeout(flushTimer);
    flushTimer = window.setTimeout(flush, INVALIDATE_DEBOUNCE_MS);
  };
  source.onerror = () => {
    if (stream !== source) return;
    // EventSource reconnects on its own; the pill just says so.
    connectionStatus.set("reconnecting");
  };
}

export function stopChangeStream(): void {
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
