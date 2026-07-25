import { writable } from "svelte/store";
import { ensureDebug } from "./debug";
import { queryClient } from "./queryClient";

// The server sends one contentless "something changed" frame per commit. The
// browser answers by marking every cached read stale; only the queries a screen
// is actually using refetch. Nothing about what changed travels over the wire.
export type ConnectionStatus = "connected" | "reconnecting";

// A burst of commits collapses into one round of refetches.
const INVALIDATE_DEBOUNCE_MS = 250;

export const connectionStatus = writable<ConnectionStatus>("reconnecting");

let stream: EventSource | null = null;
let flushTimer: number | null = null;

function invalidateEverything(): void {
  ensureDebug().flushes += 1;
  void queryClient.invalidateQueries();
}

function flush(): void {
  flushTimer = null;
  invalidateEverything();
}

export function startChangeStream(): void {
  if (stream) return;
  const source = new EventSource("/api/changes");
  stream = source;
  source.onopen = () => {
    if (stream !== source) return;
    ensureDebug().sseOpens += 1;
    connectionStatus.set("connected");
    // A connection just opened, so anything that changed while it was down is
    // still unseen: reconcile by refetching what is on screen.
    invalidateEverything();
  };
  source.onmessage = () => {
    if (stream !== source) return;
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
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer);
    flushTimer = null;
  }
  stream?.close();
  stream = null;
  connectionStatus.set("reconnecting");
}
