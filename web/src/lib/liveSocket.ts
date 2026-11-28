import { LIVE_CLOSE_CODES, LIVE_SOCKET_PATH } from "../../../server/live_updates/contracts";
import type { LiveClientFrame, LiveEnvelope, LiveSubscriptionKind } from "../../../server/live_updates/contracts";
import type { PanelsBridge } from "../../../desktop/src/bridge";
import { browserClientIdentity } from "./clientIdentity";
import { watchStream, type EndReason } from "./connectionLog";
import { STREAM_SILENCE_MS, UPDATE_RECOVERY_POLL_MS } from "./updates/config";

// One WebSocket carries every live feed of the page: the change signal and each subscription
// (navigation view, conversation view tail, conversation tail). macOS Chromium closes every
// HTTP/2 connection when a network interface address changes, which killed four event streams
// at once. A WebSocket runs on its own HTTP/1.1 connection and survives that.
//
// This module only moves frames. A consumer keeps its own recovery: after a drop, each
// subscriber is told and subscribes again as its old stream did. The server replays nothing.

/** The page's own reopen delay after a drop of a socket that had been ready. */
const REOPEN_AFTER_DROP_MS = 1_000;

/** What ended a subscription: the server's status for a refused subscribe, or null for a drop or a plain end. */
export type LiveRefusal = { status: number; detail: unknown } | null;

export interface LiveSubscriber {
  /** One payload of the feed, already parsed. `event` is the old stream's event name. */
  frame(event: string, data: unknown): void;
  /** The subscription is over. It does not come back by itself. */
  lost(refusal: LiveRefusal): void;
}

export interface LiveSocketHandlers {
  /** The server accepted the first frame. The socket is live. */
  ready(): void;
  /** The change signal. It carries nothing. */
  change(): void;
  /** The socket dropped, for any reason except "update needed". The page reopens it. */
  lost(): void;
  /** The server closed with "update needed". The page does not reopen the socket. */
  updateNeeded(): void;
}

interface Subscription {
  id: string;
  kind: LiveSubscriptionKind;
  conversationId?: string;
  params: Record<string, string>;
  subscriber: LiveSubscriber;
  sent: boolean;
}

let handlers: LiveSocketHandlers | null = null;
let socket: WebSocket | null = null;
let ready = false;
/** Whether the open socket was ever ready. A socket that never was reopens at the slower pace. */
let wasReady = false;
let lastSeq = 0;
let opening = false;
let attemptId = 0;
let silenceTimer: number | null = null;
let reopenTimer: number | null = null;
let watch: ReturnType<typeof watchStream> | null = null;
const subscriptions = new Map<string, Subscription>();
let nextSubscriptionId = 0;

export function startLiveSocket(next: LiveSocketHandlers): void {
  handlers = next;
  ensureSocket();
}

export function stopLiveSocket(): void {
  handlers = null;
  clearReopen();
  closeSocket();
}

/** The same path serves the reopen timer, the return of the network and the owner's Try now. */
export function retryLiveSocket(): void {
  clearReopen();
  closeSocket();
  ensureSocket();
}

/**
 * Subscribe to one feed. The subscribe frame goes out once the socket is ready, and the socket
 * opens first if it is not open. The returned function ends the subscription and is safe to
 * call more than once.
 */
export function subscribeLive(
  feed: { kind: LiveSubscriptionKind; conversationId?: string; params?: Record<string, string> },
  subscriber: LiveSubscriber,
): () => void {
  const subscription: Subscription = {
    id: `s${++nextSubscriptionId}`,
    kind: feed.kind,
    conversationId: feed.conversationId,
    params: feed.params ?? {},
    subscriber,
    sent: false,
  };
  subscriptions.set(subscription.id, subscription);
  if (ready) sendSubscribe(subscription);
  else ensureSocket();
  return () => {
    if (subscriptions.get(subscription.id) !== subscription) return;
    subscriptions.delete(subscription.id);
    if (subscription.sent) send({ op: "unsubscribe", id: subscription.id });
  };
}

function send(frame: LiveClientFrame): void {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(frame));
}

function sendSubscribe(subscription: Subscription): void {
  subscription.sent = true;
  send({
    op: "subscribe",
    id: subscription.id,
    kind: subscription.kind,
    ...(subscription.conversationId === undefined ? {} : { conversation_id: subscription.conversationId }),
    params: subscription.params,
  });
}

function ensureSocket(): void {
  if (socket !== null || reopenTimer !== null || opening) return;
  void openSocket();
}

/** The server address and who the app is. The desktop page cannot use its own origin for this. */
async function target(): Promise<{ url: string; identity: LiveClientFrame & { op: "hello" } } | null> {
  // The desktop app's preload gives the page its bridge. A browser has none.
  const bridge = (globalThis as { panels?: PanelsBridge }).panels;
  if (bridge !== undefined) {
    const connection = await bridge.liveConnection();
    if (connection === null) return null;
    return { url: socketUrl(connection.address), identity: { op: "hello", client: connection.identity } };
  }
  return { url: socketUrl(location.href), identity: { op: "hello", client: browserClientIdentity() } };
}

function socketUrl(base: string): string {
  const url = new URL(LIVE_SOCKET_PATH, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

async function openSocket(): Promise<void> {
  const attempt = ++attemptId;
  opening = true;
  let where: Awaited<ReturnType<typeof target>>;
  try {
    where = await target();
  } catch {
    where = null;
  }
  if (attempt !== attemptId) return;
  opening = false;
  const own = watchStream("/api/live");
  let opened: WebSocket | null = null;
  // With no server address yet (the desktop app before setup), there is nothing to connect to.
  if (where !== null) {
    try {
      opened = new WebSocket(where.url);
    } catch {
      // A refused address counts as a socket that never opened.
    }
  }
  if (where === null || opened === null) {
    own.ended("close");
    wasReady = false;
    scheduleReopen();
    loseSubscribers();
    handlers?.lost();
    return;
  }
  const hello = where.identity;
  socket = opened;
  watch = own;
  ready = false;
  wasReady = false;
  lastSeq = 0;
  armSilence(opened);
  opened.onopen = () => {
    if (socket === opened) opened.send(JSON.stringify(hello));
  };
  opened.onmessage = (message) => {
    if (socket !== opened) return;
    receive(opened, message.data);
  };
  opened.onerror = () => undefined;
  opened.onclose = (event) => {
    if (socket !== opened) return;
    if (event.code === LIVE_CLOSE_CODES.update_needed) end(opened, "close", event.code, "update_needed");
    else end(opened, "close", event.code, "drop");
  };
}

function receive(own: WebSocket, raw: unknown): void {
  let envelope: LiveEnvelope;
  try {
    envelope = JSON.parse(String(raw)) as LiveEnvelope;
  } catch {
    return end(own, "close", undefined, "drop");
  }
  // Each frame is the next number. A skipped number means that a frame was lost.
  if (envelope.seq !== lastSeq + 1) return end(own, "sequence_gap", undefined, "drop");
  lastSeq = envelope.seq;
  armSilence(own);
  if (envelope.sub === null) {
    if (envelope.kind === "ready") {
      ready = true;
      wasReady = true;
      watch?.opened();
      for (const subscription of subscriptions.values()) if (!subscription.sent) sendSubscribe(subscription);
      handlers?.ready();
    } else if (envelope.kind === "change") handlers?.change();
    return;
  }
  const subscription = subscriptions.get(envelope.sub);
  if (!subscription) return;
  if (envelope.kind === "ended") {
    subscriptions.delete(subscription.id);
    const data = envelope.data as { status?: number; detail?: unknown; reason?: string } | undefined;
    subscription.subscriber.lost(typeof data?.status === "number" ? { status: data.status, detail: data.detail } : null);
    return;
  }
  subscription.subscriber.frame(envelope.kind, envelope.data);
}

/** A socket that sends no frame for this long counts as lost. The server sends a heartbeat every 15 s. */
function armSilence(own: WebSocket): void {
  if (silenceTimer !== null) window.clearTimeout(silenceTimer);
  silenceTimer = window.setTimeout(() => {
    silenceTimer = null;
    if (socket === own) end(own, "silence", undefined, "drop");
  }, STREAM_SILENCE_MS);
}

/** The one place a socket ends. Every subscriber is told, and the consumers recover themselves. */
function end(own: WebSocket, reason: EndReason, closeCode: number | undefined, how: "drop" | "update_needed"): void {
  if (socket !== own) return;
  own.onopen = own.onmessage = own.onclose = null;
  try { own.close(); } catch { /* already closed */ }
  socket = null;
  ready = false;
  if (silenceTimer !== null) window.clearTimeout(silenceTimer);
  silenceTimer = null;
  watch?.ended(reason, { close_code: closeCode, last_seq: lastSeq });
  watch = null;
  if (how === "update_needed") {
    loseSubscribers();
    return handlers?.updateNeeded();
  }
  scheduleReopen();
  loseSubscribers();
  handlers?.lost();
}

/** Close the socket without reporting a drop: the page itself asked for it. */
function closeSocket(): void {
  attemptId += 1;
  opening = false;
  const own = socket;
  if (own === null) return;
  own.onopen = own.onmessage = own.onclose = null;
  try { own.close(); } catch { /* already closed */ }
  socket = null;
  ready = false;
  if (silenceTimer !== null) window.clearTimeout(silenceTimer);
  silenceTimer = null;
  watch?.ended("close", { last_seq: lastSeq });
  watch = null;
  loseSubscribers();
}

/** Every subscriber is told once. A subscriber that subscribes again waits for the next socket. */
function loseSubscribers(): void {
  const lost = [...subscriptions.values()];
  subscriptions.clear();
  for (const subscription of lost) subscription.subscriber.lost(null);
}

/** The page reopens a lost socket while the change stream runs or a subscriber waits. */
function scheduleReopen(): void {
  if ((handlers === null && subscriptions.size === 0) || reopenTimer !== null) return;
  reopenTimer = window.setTimeout(() => {
    reopenTimer = null;
    ensureSocket();
  }, wasReady ? REOPEN_AFTER_DROP_MS : UPDATE_RECOVERY_POLL_MS);
}

function clearReopen(): void {
  if (reopenTimer !== null) window.clearTimeout(reopenTimer);
  reopenTimer = null;
}
