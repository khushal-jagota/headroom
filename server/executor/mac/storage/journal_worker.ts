/** Own SQLite, codecs, full snapshots, and one ordered operation chain. */
import { parentPort, workerData } from 'node:worker_threads';
import { FILE_CHUNK_BYTES, MAX_MAILBOX_POLL_BYTES } from '../configuration.ts';
import {
  JOURNAL_OPERATIONS,
  JOURNAL_CURSORS,
  JOURNAL_READERS,
  JOURNAL_INPUT_BYTES,
} from './journal_limits.ts';
import { JournalEngine } from './journal_engine.ts';
import type { EncodedJournalRow } from './journal_engine.ts';
import { journal_error, journal_error_properties } from './journal_worker_contracts.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type {
  JournalAction,
  JournalMainMessage,
  JournalWorkerData,
  JournalWorkerMessage,
  CursorRowDescriptor,
  ReaderDescriptor,
} from './journal_worker_contracts.ts';
import type { RequiredJournalProtocol, JournalOutputPolicy } from './journal_contracts.ts';
const port = parentPort!,
  data = workerData as JournalWorkerData;
let engine: JournalEngine | undefined,
  sealed = false,
  sequence = 0;
const callbacks = new Map<number, { resolve(value?: bigint): void; reject(error: Error): void }>();
const inputs = new Map<
  number,
  { text: boolean; chunks: (ArrayBuffer | string)[]; bytes: number; value?: Buffer | string }
>();
const readers = new Map<number, { bytes: Buffer; offset: number; cursor?: number }>();
const cursors = new Map<
  number,
  { rows: (EncodedJournalRow | undefined)[]; offset: number; children: Set<number> }
>();
function callback(callback: 'permission' | 'flush' | 'clock'): Promise<bigint | undefined> {
  const id = ++sequence;
  const task = new Promise<bigint | undefined>((resolve, reject) =>
    callbacks.set(id, { resolve, reject }),
  );
  port.postMessage({ kind: 'callback', id, callback } satisfies JournalWorkerMessage);
  return task;
}
function reader(bytes: Buffer, cursor?: number): ReaderDescriptor {
  if (readers.size >= JOURNAL_READERS) throw new Error('journal reader capacity reached');
  const id = ++sequence;
  readers.set(id, { bytes, offset: 0, cursor });
  if (cursor !== undefined) cursors.get(cursor)!.children.add(id);
  return { reader: id, byte_count: bytes.length };
}
function close_reader(id: number): void {
  const value = readers.get(id);
  readers.delete(id);
  if (value?.cursor !== undefined) {
    const cursor = cursors.get(value.cursor);
    cursor?.children.delete(id);
    if (cursor && cursor.rows.length === 0 && cursor.children.size === 0)
      cursors.delete(value.cursor);
  }
}
function close_cursor(id: number): void {
  const value = cursors.get(id);
  if (!value) return;
  for (const child of [...value.children]) close_reader(child);
  cursors.delete(id);
}
function cursor(rows: EncodedJournalRow[]): { cursor: number } {
  require_cursor();
  const id = ++sequence;
  cursors.set(id, { rows, offset: 0, children: new Set() });
  return { cursor: id };
}
function require_cursor(): void {
  if (cursors.size >= JOURNAL_CURSORS) throw new Error('journal cursor capacity reached');
}
function take_input(id: number): Buffer | string {
  const input = inputs.get(id);
  inputs.delete(id);
  if (input?.value === undefined) throw new Error('journal input is incomplete or closed');
  return input.value;
}
const ready = (async () => {
  const [protocol, policy] = await Promise.all([
    import(data.protocol_module),
    import(data.outputs_module),
  ]);
  if (
    typeof protocol.decode_json !== 'function' ||
    typeof protocol.encode_value !== 'function' ||
    typeof policy.command_owes_outputs !== 'function'
  )
    throw new TypeError('journal requires the shared codec and pure outputs policy');
  engine = new JournalEngine(
    protocol as RequiredJournalProtocol,
    policy as JournalOutputPolicy,
    data.incarnation,
    () => {
      if (Atomics.load(new Int32Array(data.health_fence), 0))
        throw new ServerLifecycleError('Spool resource failure seals journal mutations.');
    },
  );
  port.postMessage({ kind: 'ready' } satisfies JournalWorkerMessage);
})();
void ready.catch((error) =>
  port.postMessage({
    kind: 'startup_error',
    error: journal_error(error),
  } satisfies JournalWorkerMessage),
);
async function execute(action: JournalAction): Promise<unknown> {
  switch (action.operation) {
    case 'open':
      await callback('permission');
      if (sealed) throw new Error('journal opening was stopped');
      if (engine!.open(data.root)) await callback('flush');
      if (sealed) throw new Error('journal opening was stopped');
      engine!.recover();
      return;
    case 'poll_cursor':
      return engine!.poll_cursor();
    case 'lower_poll_cursor':
      return engine!.lower_poll_cursor(action.high, action.cursor_at_lease);
    case 'record_poll_reply': {
      require_cursor();
      const raw = take_input(action.input);
      if ((typeof raw === 'string' ? Buffer.byteLength(raw) : raw.length) > MAX_MAILBOX_POLL_BYTES)
        throw new RangeError('Mailbox poll reply exceeds its body limit.');
      const now = await callback('clock');
      const result = engine!.record_poll_reply(raw, action.incarnation, action.after, now!);
      return {
        ...cursor(result.rows),
        command_count: result.command_count,
        fresh_count: result.rows.length,
      };
    }
    case 'result_of': {
      if (readers.size >= JOURNAL_READERS) throw new Error('journal reader capacity reached');
      const result = engine!.result_of(action.command_id);
      return result === null ? null : reader(result);
    }
    case 'finish':
      engine!.finish(action.command_id, take_input(action.input), action.owed);
      return;
    case 'mark_outputs_published':
      engine!.mark_outputs_published(action.command_id);
      return;
    case 'unpublished':
      require_cursor();
      return cursor(engine!.unpublished());
    case 'unreported':
      require_cursor();
      return cursor(engine!.unreported(action.limit));
    case 'mark_reported':
      engine!.mark_reported(action.command_ids);
      return;
    case 'select_report': {
      if (readers.size >= JOURNAL_READERS) throw new Error('journal reader capacity reached');
      const selected = engine!.select_report();
      return selected === null
        ? null
        : {
            selection: selected.selection,
            command_ids: selected.command_ids,
            body: reader(selected.body),
          };
    }
    case 'report_feedback':
      return engine!.report_feedback(action.selection, action.settled_ids);
    case 'shrink_to_tombstones':
      engine!.shrink_to_tombstones();
      return;
    case 'input_start': {
      if (inputs.size) throw new Error('journal already has a partial input');
      const id = ++sequence;
      inputs.set(id, { text: action.text, chunks: [], bytes: 0 });
      return id;
    }
    case 'input_push': {
      const input = inputs.get(action.input);
      if (!input) throw new Error('journal input is closed');
      const size =
        typeof action.chunk === 'string'
          ? Buffer.byteLength(action.chunk)
          : action.chunk.byteLength;
      if (!size || size > FILE_CHUNK_BYTES || input.bytes + size > JOURNAL_INPUT_BYTES)
        throw new Error('journal input exceeds its chunk or native byte limit');
      input.bytes += size;
      input.chunks.push(action.chunk);
      return;
    }
    case 'input_end': {
      const input = inputs.get(action.input);
      if (!input) throw new Error('journal input is closed');
      input.value = input.text
        ? input.chunks.join('')
        : Buffer.concat(input.chunks.map((chunk) => Buffer.from(chunk as ArrayBuffer)));
      input.chunks = [];
      return;
    }
    case 'input_drop':
      inputs.delete(action.input);
      return;
    case 'cursor_close':
      close_cursor(action.cursor);
      return;
    case 'cursor_next': {
      const item = cursors.get(action.cursor);
      if (!item) return null;
      const row = item.rows[item.offset];
      if (row === undefined) {
        item.rows = [];
        if (item.children.size === 0) cursors.delete(action.cursor);
        return null;
      }
      if (readers.size + Number(!!row.command) + Number(!!row.result) > JOURNAL_READERS)
        throw new Error('journal reader capacity reached');
      item.rows[item.offset++] = undefined;
      const value: CursorRowDescriptor = { command_id: row.command_id };
      if (row.command) value.command = reader(row.command, action.cursor);
      if (row.result) value.result = reader(row.result, action.cursor);
      return value;
    }
    case 'reader_close':
      close_reader(action.reader);
      return;
    case 'reader_next': {
      const item = readers.get(action.reader);
      if (!item) return null;
      if (item.offset === item.bytes.length) {
        close_reader(action.reader);
        return null;
      }
      const count = Math.min(FILE_CHUNK_BYTES, item.bytes.length - item.offset);
      const copy = new Uint8Array(count);
      copy.set(item.bytes.subarray(item.offset, item.offset + count));
      item.offset += count;
      if (item.offset === item.bytes.length) close_reader(action.reader);
      return copy.buffer;
    }
  }
}
let chain = ready.catch(() => {});
let queued = 0;
function reply(id: number, value?: unknown, error?: unknown): void {
  const message: JournalWorkerMessage =
    error === undefined
      ? { kind: 'reply', id, value }
      : { kind: 'reply', id, error: journal_error(error) };
  try {
    port.postMessage(message, value instanceof ArrayBuffer ? [value] : []);
  } catch (error) {
    queueMicrotask(() => {
      throw error;
    });
  }
}
port.on('message', (message: JournalMainMessage) => {
  if (message.kind === 'callback_result') {
    const pending = callbacks.get(message.id);
    callbacks.delete(message.id);
    if (message.error)
      pending?.reject(
        Object.assign(
          new Error(message.error.message),
          { name: message.error.name },
          journal_error_properties(message.error),
        ),
      );
    else pending?.resolve(message.value);
    return;
  }
  if (message.kind === 'stop') {
    sealed = true;
    chain = chain.then(() => {
      try {
        readers.clear();
        cursors.clear();
        inputs.clear();
        engine?.close();
        reply(message.id);
        port.close();
      } catch (error) {
        reply(message.id, undefined, error);
      }
    });
    return;
  }
  if (sealed) {
    reply(message.id, undefined, new Error('journal is stopped'));
    return;
  }
  if (queued >= JOURNAL_OPERATIONS) {
    reply(message.id, undefined, new Error('journal operation capacity reached'));
    return;
  }
  queued++;
  chain = chain.then(async () => {
    let value: unknown;
    try {
      await ready;
      value = await execute(message.action);
    } catch (error) {
      reply(message.id, undefined, error);
      return;
    } finally {
      queued--;
    }
    // A failed successful-response transfer can hide a committed command. Let that
    // fatal transport error exit the worker instead of returning an ordinary error.
    reply(message.id, value);
  });
});
