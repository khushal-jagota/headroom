import type { StreamCloseRecord } from '../../core/http/live_streams/records.ts';

/** Version 1 of what a client reports. Only `at` and `kind` are required. */
export const CLIENT_EVENTS_VERSION = 1;
export const CLIENT_EVENT_KINDS = [
  'stream_ended',
  'silence',
  'probe_failed',
  'probe_ok',
  'power',
  'online',
] as const;
export const CLIENT_EVENT_FIELDS = {
  stream: 'string',
  reason: 'string',
  open_ms: 'number',
  online: 'boolean',
  visible: 'boolean',
  drift_ms: 'number',
  probe_ms: 'number',
  power: 'string',
} as const;
export const MAX_CLIENT_EVENTS_PER_BATCH = 50;
/** One device may send this many batches each minute. */
export const CLIENT_EVENT_BATCHES_PER_MINUTE = 12;

export interface ClientEventRecord {
  at: number;
  kind: string;
  [field: string]: unknown;
}
export interface ConnectionRecordQuery {
  /** Epoch milliseconds, since inclusive and until exclusive. */
  since: number;
  until: number;
  device_id: string | null;
  limit: number;
}
export interface ConnectionRecords {
  stream_closes: StreamCloseRecord[];
  client_events: Array<
    ClientEventRecord & { received_at: number; device_id: string; client_kind: string }
  >;
}

/** A fixed one-minute window for each device. Only the server's own memory holds it. */
export class DeviceRateLimit {
  readonly #windows = new Map<string, { start: number; count: number }>();
  readonly limit: number;
  readonly window_ms: number;
  constructor(limit: number, window_ms = 60_000) {
    this.limit = limit;
    this.window_ms = window_ms;
  }
  admit(device_id: string, now: number): boolean {
    for (const [key, held] of this.#windows)
      if (now - held.start >= this.window_ms) this.#windows.delete(key);
    const held = this.#windows.get(device_id);
    if (!held) {
      this.#windows.set(device_id, { start: now, count: 1 });
      return true;
    }
    if (held.count >= this.limit) return false;
    held.count += 1;
    return true;
  }
}

/** Returns the events that pass, or the reason for the whole batch to fail with 422. */
export function parse_client_events(body: unknown): ClientEventRecord[] {
  const batch = body as { version?: unknown; events?: unknown } | null;
  if (batch === null || typeof batch !== 'object' || Array.isArray(batch))
    throw new RangeError('body must be an object');
  if (batch.version !== CLIENT_EVENTS_VERSION)
    throw new RangeError(`version must be ${CLIENT_EVENTS_VERSION}`);
  if (!Array.isArray(batch.events) || batch.events.length > MAX_CLIENT_EVENTS_PER_BATCH)
    throw new RangeError(`events must be a list of at most ${MAX_CLIENT_EVENTS_PER_BATCH}`);
  return batch.events.map((event: unknown, index) => {
    const value = event as Record<string, unknown> | null;
    if (value === null || typeof value !== 'object' || Array.isArray(value))
      throw new RangeError(`events[${index}] must be an object`);
    if (!Number.isSafeInteger(value.at) || (value.at as number) < 0)
      throw new RangeError(`events[${index}].at must be epoch milliseconds`);
    if (!(CLIENT_EVENT_KINDS as readonly string[]).includes(value.kind as string))
      throw new RangeError(`events[${index}].kind is not a known kind`);
    const kept: ClientEventRecord = { at: value.at as number, kind: value.kind as string };
    for (const [field, type] of Object.entries(CLIENT_EVENT_FIELDS)) {
      const held = value[field];
      if (held === undefined || held === null) continue;
      if (typeof held !== type || (typeof held === 'string' && held.length > 200))
        throw new RangeError(`events[${index}].${field} must be a short ${type}`);
      if (typeof held === 'number' && !Number.isFinite(held))
        throw new RangeError(`events[${index}].${field} must be finite`);
      kept[field] = held;
    }
    return kept;
  });
}
