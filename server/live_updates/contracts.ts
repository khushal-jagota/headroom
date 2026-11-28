/**
 * The live-updates WebSocket contract. `docs/live-updates.md` describes it in prose.
 *
 * A client opens `LIVE_SOCKET_PATH`, sends one `hello` frame, and receives `ready`. After that it
 * receives change pings, and it can open and close subscriptions. Every server frame is one
 * `LiveEnvelope`. A subscription frame carries today's payload, unchanged, in `data`.
 */
import type { ClientIdentity } from '../client_versions/contracts.ts';

export const LIVE_SOCKET_PATH = '/api/live';

/** The three named closes. Every other close, including 1006, is a drop. */
export const LIVE_CLOSE_CODES = {
  update_needed: 4001,
  server_restarting: 4002,
  bad_first_frame: 4003,
} as const;
/** A socket that falls too far behind is closed as a drop, and its client reconnects. */
export const LIVE_BACKPRESSURE_CLOSE_CODE = 1013;
/** Any other frame that breaks the contract after `ready` closes the socket as a drop. */
export const LIVE_PROTOCOL_CLOSE_CODE = 1008;

export const LIVE_HEARTBEAT_MS = 15_000;
export const LIVE_FIRST_FRAME_MS = 10_000;
/** The navigation view request alone can be 128 KiB. */
export const LIVE_MAX_CLIENT_FRAME_BYTES = 192 * 1024;
export const LIVE_MAX_BUFFERED_BYTES = 16 * 1024 * 1024;

export type LiveSubscriptionKind = 'navigation' | 'conversation_view' | 'conversation_events';

/** Client to server. `params` holds the query parameters that the old stream URL took. */
export type LiveClientFrame =
  | { op: 'hello'; client: ClientIdentity }
  | {
      op: 'subscribe';
      id: string;
      kind: LiveSubscriptionKind;
      conversation_id?: string;
      params?: Record<string, string>;
    }
  | { op: 'unsubscribe'; id: string };

/**
 * Server to client. `seq` starts at 1 on each socket and grows by one for each frame, so a
 * skipped number means a drop. `sub` is null for socket frames: `ready`, `heartbeat`, `change`.
 * For a subscription, `kind` is the old stream's event name, and `ended` closes it. `ended`
 * carries `{ reason: 'closed' }`, or `{ status, detail }` when the subscribe failed.
 */
export interface LiveEnvelope {
  seq: number;
  sub: string | null;
  kind: string;
  data?: unknown;
}

/** One payload of a feed. `data` is JSON text, written to the old stream and the socket alike. */
export interface LiveFrame {
  event: string;
  data: string;
}

export interface LiveSink {
  /** A null frame is a quiet keep-alive. False means that the caller must wait for `drain`. */
  write(frame: LiveFrame | null): boolean;
  drain(signal: AbortSignal): Promise<void>;
  end(): void;
  on_closed(callback: () => void): void;
}

export interface LiveSubscribeRequest {
  id: string;
  conversation_id: string | null;
  params: Record<string, string>;
}
export interface LiveSubscriptionHandle {
  close(): void;
}
/** Throws `HttpError` or `PlannerError` when the subscribe fails, as the old route answered. */
export type LiveOpener = (
  request: LiveSubscribeRequest,
  sink: LiveSink,
) => LiveSubscriptionHandle | Promise<LiveSubscriptionHandle>;
