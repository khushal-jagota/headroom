import type { FastifyReply } from 'fastify';
import type { ClientIdentity } from '../../../client_versions/contracts.ts';

/**
 * Who ended a live stream. `server_ended` is a stream that the route finished by itself.
 * `write_error` is any error on the response other than a reset from the peer.
 */
export type StreamClosedBy = 'peer' | 'server_pre_close' | 'write_error' | 'server_ended';

/** One record for each live stream, written when it closes. Times are epoch milliseconds. */
export interface StreamCloseRecord {
  route: string;
  client_kind: string;
  device_id: string | null;
  opened_at: number;
  closed_at: number;
  age_ms: number;
  /** Bytes handed to the response, not bytes that the peer read. */
  bytes_written: number;
  closed_by: StreamClosedBy;
  /** Time from the last write to the close. A stream with no write counts from its open. */
  last_write_ago_ms: number;
  error_code: string | null;
  /** A WebSocket close writes these two. An event stream leaves them out. */
  close_code?: number | null;
  /** The sequence number of the last frame that the server sent on the socket. */
  last_sequence?: number | null;
}
export type StreamCloseSink = (record: StreamCloseRecord) => void;

export interface StreamCloseTracking {
  client: ClientIdentity | null;
  route: string;
  sink?: StreamCloseSink;
  /** True once the server began to shut down, so the streams that it destroys read as its own. */
  shutting_down: () => boolean;
  now?: () => number;
}

function error_code(error: unknown): string {
  const value = error as { code?: unknown; name?: unknown } | null;
  if (typeof value?.code === 'string') return value.code;
  return typeof value?.name === 'string' ? value.name : 'unknown';
}

/**
 * Watches one live response and writes its close record once. Every live stream route reaches
 * this through `count_live_stream`, so no route needs its own bookkeeping.
 */
export function track_stream_close(reply: FastifyReply, tracking: StreamCloseTracking): void {
  const now = tracking.now ?? Date.now;
  const raw = reply.raw;
  const opened_at = now();
  let bytes_written = 0;
  let last_write_at = opened_at;
  let finished = false;
  let failure: string | null = null;
  const write = raw.write.bind(raw) as (...args: unknown[]) => boolean;
  raw.write = ((chunk: unknown, ...rest: unknown[]) => {
    bytes_written +=
      typeof chunk === 'string'
        ? Buffer.byteLength(
            chunk,
            typeof rest[0] === 'string' ? (rest[0] as BufferEncoding) : 'utf8',
          )
        : ((chunk as Uint8Array | undefined)?.byteLength ?? 0);
    last_write_at = now();
    return write(chunk, ...rest);
  }) as typeof raw.write;
  const fail = (error: unknown) => {
    failure ??= error_code(error);
  };
  raw.once('error', fail);
  raw.socket?.once('error', fail);
  raw.once('finish', () => {
    finished = true;
  });
  raw.once('close', () => {
    const closed_at = now();
    // A reset is the peer's own act, so it reads as `peer`. The code stays in `error_code`.
    const closed_by: StreamClosedBy =
      failure !== null && failure !== 'ECONNRESET'
        ? 'write_error'
        : tracking.shutting_down()
          ? 'server_pre_close'
          : finished
            ? 'server_ended'
            : 'peer';
    const record: StreamCloseRecord = {
      route: tracking.route,
      client_kind: tracking.client?.kind ?? 'unknown',
      device_id: tracking.client?.device_id ?? null,
      opened_at,
      closed_at,
      age_ms: closed_at - opened_at,
      bytes_written,
      closed_by,
      last_write_ago_ms: closed_at - last_write_at,
      error_code: failure,
    };
    report_stream_close(record, tracking.sink);
  });
}

/** The journal line and the stored record. Event streams and sockets write them alike. */
export function report_stream_close(record: StreamCloseRecord, sink?: StreamCloseSink): void {
  console.log(`stream_close ${JSON.stringify(record)}`);
  try {
    sink?.(record);
  } catch (error) {
    console.warn('stream_close record was not stored', error);
  }
}
