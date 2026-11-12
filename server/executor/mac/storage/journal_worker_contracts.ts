/** Private control and bounded chunk transport. No executor message shapes are cloned. */
import { JsonNumber } from '../../../core/http/numeric_json.ts';
export interface JournalWorkerData {
  root: string;
  incarnation: string;
  protocol_module: string;
  outputs_module: string;
  health_fence: SharedArrayBuffer;
}
export type JournalAction =
  | { operation: 'open' | 'poll_cursor' | 'unpublished' | 'shrink_to_tombstones' }
  | { operation: 'lower_poll_cursor'; high: bigint; cursor_at_lease: bigint }
  | { operation: 'record_poll_reply'; input: number; incarnation: string; after: bigint }
  | { operation: 'result_of' | 'mark_outputs_published'; command_id: string }
  | { operation: 'finish'; command_id: string; input: number; owed: boolean }
  | { operation: 'unreported'; limit: number }
  | { operation: 'mark_reported'; command_ids: readonly string[] }
  | { operation: 'input_start'; text: boolean }
  | { operation: 'input_push'; input: number; chunk: ArrayBuffer | string }
  | { operation: 'input_end' | 'input_drop'; input: number }
  | { operation: 'cursor_next' | 'cursor_close'; cursor: number }
  | { operation: 'reader_next' | 'reader_close'; reader: number };
type Diagnostic =
  | { tag: 'numeric'; kind: 'integer' | 'float'; integer: bigint | null; value: number }
  | { tag: 'array'; items: Diagnostic[] }
  | { tag: 'object'; entries: [string, Diagnostic][] }
  | { tag: 'bytes'; value: Uint8Array; buffer: boolean }
  | { tag: 'scalar'; value: unknown };
function pack(value: unknown): Diagnostic {
  if (value instanceof JsonNumber)
    return { tag: 'numeric', kind: value.kind, integer: value.integer, value: value.value };
  if (value instanceof Uint8Array)
    return { tag: 'bytes', value: Uint8Array.from(value), buffer: Buffer.isBuffer(value) };
  if (Array.isArray(value)) return { tag: 'array', items: value.map(pack) };
  if (value !== null && typeof value === 'object')
    return {
      tag: 'object',
      entries: Object.entries(value).map(([key, item]) => [key, pack(item)]),
    };
  return { tag: 'scalar', value };
}
function unpack(value: Diagnostic): unknown {
  switch (value.tag) {
    case 'numeric':
      return Object.freeze(
        Object.assign(Object.create(JsonNumber.prototype), {
          kind: value.kind,
          integer: value.integer,
          value: value.value,
        }),
      );
    case 'array':
      return value.items.map(unpack);
    case 'object':
      return Object.fromEntries(value.entries.map(([key, item]) => [key, unpack(item)]));
    case 'bytes':
      return value.buffer ? Buffer.from(value.value) : value.value;
    case 'scalar':
      return value.value;
  }
}
export interface JournalErrorBody {
  name: string;
  message: string;
  properties: Diagnostic;
}
export function journal_error_properties(error: JournalErrorBody): Record<string, unknown> {
  return unpack(error.properties) as Record<string, unknown>;
}
export type JournalMainMessage =
  | { kind: 'request'; id: number; action: JournalAction }
  | { kind: 'stop'; id: number }
  | { kind: 'callback_result'; id: number; value?: bigint; error?: JournalErrorBody };
export interface ReaderDescriptor {
  reader: number;
  byte_count: number;
}
export interface CursorRowDescriptor {
  command_id: string;
  command?: ReaderDescriptor;
  result?: ReaderDescriptor;
}
export type JournalWorkerMessage =
  | { kind: 'ready' }
  | { kind: 'startup_error'; error: JournalErrorBody }
  | { kind: 'reply'; id: number; value?: unknown; error?: JournalErrorBody }
  | { kind: 'callback'; id: number; callback: 'permission' | 'flush' | 'clock' };
export function journal_error(error: unknown): JournalErrorBody {
  const value = error instanceof Error ? error : new Error(String(error));
  const properties = Object.fromEntries(
    Object.entries(value).filter(
      ([key, item]) =>
        !['database_cleanup', 'cleanup', 'cleanup_error'].includes(key) &&
        typeof item !== 'function',
    ),
  );
  return { name: value.name, message: value.message, properties: pack(properties) };
}
