/** Unique storage risks; populated parity and process-crash checks live in the direct driver. */
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Worker } from 'node:worker_threads';
import BetterSqlite3 from 'better-sqlite3';
import { afterEach, expect, test, vi } from 'vitest';
import { ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import {
  ProtocolRefused,
  ExecutorValidationError,
  decode_json,
  encode_bounded,
} from '../../protocol.ts';
import { MacPresence } from '../transport/presence.ts';
import { MAX_ENVELOPE_BYTES } from '../configuration.ts';
import { acquire_process_lock } from '../platform.ts';
import { DATABASE_NAME } from './schema.ts';
import { OutboxStore } from './outbox.ts';
import { delivery_state } from './delivery.ts';
import { row_envelope } from './envelopes.ts';
import type { ItemColumns } from './contracts.ts';
import type { OutboxCleanup } from './resources.ts';

const roots: string[] = [],
  stores: OutboxCleanup[] = [];
const clock = {
  monotonic: () => performance.now() / 1000,
  now_ns: () => BigInt(Date.now()) * 1_000_000n,
};
const deadline = () => ({ deadline: performance.now() / 1000 + 10 });
const expected_failures = new WeakSet<OutboxCleanup>();
function messages(error: unknown): string[] {
  if (!(error instanceof Error)) return [String(error)];
  return [
    error.message,
    ...(error instanceof AggregateError ? error.errors.flatMap(messages) : []),
    ...(error.cause === undefined ? [] : messages(error.cause)),
  ];
}
async function retained_failure(task: Promise<unknown>, message?: string): Promise<void> {
  const error = await task.then(
    () => null,
    (failure) => failure,
  );
  expect(error).toBeInstanceOf(Error);
  expect(error).not.toBeInstanceOf(ShutdownDeadlineExceeded);
  if (message) expect(messages(error)).toContain(message);
}
async function actual_failure(owner: OutboxCleanup, message: string): Promise<void> {
  expected_failures.add(owner);
  await retained_failure(
    observe_shutdown(owner.settled(), deadline(), 'actual cleanup stalled'),
    message,
  );
}
const payload = Buffer.from('{"payload":{},"files":[]}');
function columns(submission_id: string): ItemColumns {
  return { submission_id, conversation_id: 'conversation', child_id: 'child', kind: 'cli_write' };
}
function scratch() {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-outbox-')));
  roots.push(root);
  return root;
}
async function fixture() {
  const root = scratch(),
    spool = join(root, 'spool'),
    artifacts = join(root, 'artifacts', 'tickets', 'ticket');
  mkdirSync(artifacts, { recursive: true });
  const on_failure = vi.fn();
  const store = await OutboxStore.open(spool, clock, deadline(), on_failure);
  stores.push(store);
  return { root, spool, artifacts, store, on_failure };
}
afterEach(async () => {
  vi.restoreAllMocks();
  for (const store of stores.splice(0).reverse()) {
    if (expected_failures.has(store)) {
      await retained_failure(store.stop(deadline()));
      await retained_failure(
        observe_shutdown(store.settled(), deadline(), 'actual cleanup stalled'),
      );
    } else {
      await store.stop(deadline());
      await observe_shutdown(store.settled(), deadline(), 'actual cleanup stalled');
    }
  }
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

test('an incompatible version-zero store refuses before a lock, WAL, or byte mutation', async () => {
  const spool = join(scratch(), 'spool');
  mkdirSync(spool);
  const path = join(spool, DATABASE_NAME);
  writeFileSync(path, '');
  const before = readFileSync(path);
  await expect(OutboxStore.open(spool, clock, deadline(), () => {})).rejects.toThrow(
    'unsupported outbox format',
  );
  expect(readFileSync(path)).toEqual(before);
  expect(readdirSync(spool)).toEqual([DATABASE_NAME]);
});

test('failed reader initialization retains both unreturned connections and their main lock', async () => {
  const spool = join(scratch(), 'spool'),
    path = join(spool, DATABASE_NAME);
  const pragma = BetterSqlite3.prototype.pragma,
    close = BetterSqlite3.prototype.close;
  const connections = new Set<BetterSqlite3.Database>();
  const configuring = vi.spyOn(BetterSqlite3.prototype, 'pragma').mockImplementation(function (
    this: BetterSqlite3.Database,
    ...args: Parameters<BetterSqlite3.Database['pragma']>
  ) {
    if (this.readonly && this.name === path && args[0] === 'query_only = ON')
      throw new Error('initial PRAGMA failed');
    return pragma.call(this, ...args);
  });
  const closing = vi.spyOn(BetterSqlite3.prototype, 'close').mockImplementation(function (
    this: BetterSqlite3.Database,
  ) {
    if (this.name === path) {
      connections.add(this);
      throw new Error('unreturned database connection cannot close');
    }
    return close.call(this);
  });
  const failure = await OutboxStore.open(spool, clock, deadline(), () => {}).then(
    (store) => {
      stores.push(store);
      throw new Error('startup unexpectedly succeeded');
    },
    (error) => error as Error & { cleanup?: OutboxCleanup; cause?: Error },
  );
  expect(failure.cleanup).toBeDefined();
  const cleanup = failure.cleanup!;
  stores.push(cleanup);
  expect(failure.cause?.message).toBe('initial PRAGMA failed');
  expect([...connections].map((connection) => connection.readonly).sort()).toEqual([false, true]);
  expect(() => acquire_process_lock(spool)).toThrow('outbox is already owned');
  configuring.mockRestore();
  closing.mockRestore();
  await retained_failure(cleanup.stop(deadline()), 'unreturned database connection cannot close');
  await actual_failure(cleanup, 'unreturned database connection cannot close');
  expect([...connections].every((connection) => !connection.open)).toBe(true);
  const lease = acquire_process_lock(spool);
  lease.close();
});

test('the full-item limit rolls back row allocation even with a no-op validator', async () => {
  const { store } = await fixture();
  const large = Buffer.from(
    JSON.stringify({ payload: { text: 'x'.repeat(MAX_ENVELOPE_BYTES - 100) }, files: [] }),
  );
  expect(large.byteLength).toBeLessThan(MAX_ENVELOPE_BYTES);
  let refusal: unknown;
  try {
    store.append(columns('oversize'), large, () => {});
  } catch (error) {
    refusal = error;
  }
  expect(refusal).toBeInstanceOf(ProtocolRefused);
  expect(refusal).toBeInstanceOf(ExecutorValidationError);
  expect(refusal).toMatchObject({ message: 'value exceeds its encoded size limit' });
  expect(store.lookup('oversize')).toBeNull();
  expect(store.append(columns('accepted'), payload, () => {}).sequence).toBe(1);
});

test('a restart retains the delivery deadline and a failed backend event after child cleanup', async () => {
  const { spool, store } = await fixture();
  const wall = vi.spyOn(clock, 'now_ns').mockReturnValue(1_700_000_000_000_000_000n);
  store.append(
    { ...columns('failed'), kind: 'backend_event' },
    Buffer.from(JSON.stringify({ payload: { message: 'preserve this agent message' }, files: [] })),
    () => {},
  );
  expect(store.delivery_started('failed')).toBe(1_700_000_000_000);
  wall.mockReturnValue(1_700_000_120_000_000_000n);
  expect(store.delivery_started('failed')).toBe(1_700_000_000_000);
  await store.stop(deadline());
  const resumed = await OutboxStore.open(spool, clock, deadline(), () => {});
  stores.push(resumed);
  expect(resumed.delivery_started('failed')).toBe(1_700_000_000_000);
  const receipt = Buffer.from('{"local_delivery_failure":true}');
  const confirmed = vi.fn(() => {
    expect(resumed.lookup('failed')?.receipt_json).toEqual(receipt);
  });
  await resumed.fail_delivery('failed', receipt, 'HTTP 503 remained unavailable', confirmed);
  expect(confirmed).toHaveBeenCalledOnce();
  expect(resumed.pending_count()).toBe(0);
  resumed.append({ ...columns('stop'), kind: 'child_stopped' }, payload, () => {});
  await resumed.acknowledge('stop', resumed.sender_id, Buffer.from('{}'), () => {});
  await resumed.stop(deadline());
  const reopened = await OutboxStore.open(spool, clock, deadline(), () => {});
  stores.push(reopened);
  const retained = reopened.lookup('failed')!;
  expect(JSON.parse(retained.payload_json.toString()).payload).toEqual({
    message: 'preserve this agent message',
  });
  expect(delivery_state(retained)).toEqual({
    started_at_ms: 1_700_000_000_000,
    file_refusals: [],
    failure: 'HTTP 503 remained unavailable',
  });
  expect(reopened.delivery_status()).toEqual({
    failed_count: 1,
    refused_file_count: 0,
    last_error: 'HTTP 503 remained unavailable',
  });
  const presence = new MacPresence(
    {
      application_revision: 'test',
      executor_incarnation: 'incarnation',
      protocol_version: 26,
      spool_format_version: 5,
    },
    () => 1_700_000_180_000,
    42,
    () => ({ ...reopened.delivery_status() }),
  );
  presence.failed('lease was offline');
  presence.accepted();
  const status = decode_json(
    encode_bounded(presence.status(false), 'MacLocalStatus'),
    'MacLocalStatus',
  );
  expect(status.last_lease_failure).toBeNull();
  expect(status.outbox_delivery).toEqual({
    failed_count: 1,
    refused_file_count: 0,
    last_error: 'HTTP 503 remained unavailable',
  });
});

test('an old waiting item hands its files to the file sync, and old file copies go', async () => {
  const { spool, store } = await fixture();
  const held = { ticket_id: 'ticket', byte_count: 1, modified_ns: 5n };
  const waiting = { relative_path: 'artifacts/waiting.txt', sha256: 'a'.repeat(64) };
  const delivered = { relative_path: 'artifacts/delivered.txt', sha256: 'b'.repeat(64) };
  const row = store.append(
    { ...columns('waiting'), kind: 'backend_event' },
    Buffer.from(
      JSON.stringify({
        payload: {},
        files: [{ scope: 'ticket', owner_id: 'ticket', byte_count: 1, ...waiting }],
      }),
    ),
    () => {},
  );
  store.record_home_file({ ...held, ...waiting });
  store.record_home_file({ ...held, ...delivered });
  await store.stop(deadline());
  // An older runtime leaves this state: the item still owns a file that never reached the home.
  const old = new BetterSqlite3(join(spool, DATABASE_NAME));
  old
    .prepare('INSERT INTO item_files VALUES(?,?,?,?)')
    .run(row.sequence, waiting.relative_path, waiting.sha256, 1);
  old.close();
  mkdirSync(join(spool, 'blobs'), { recursive: true });
  writeFileSync(join(spool, 'blobs', waiting.sha256), 'x');

  const reopened = await OutboxStore.open(spool, clock, deadline(), () => {});
  stores.push(reopened);
  expect(reopened.home_files('ticket')).toEqual([{ ...held, ...delivered }]);
  expect(row_envelope(reopened.sender_id, reopened.pending()[0]!).file_dependencies).toEqual([]);
  expect(readdirSync(join(spool, 'blobs'))).toEqual([]);
  reopened.forget_home_file('ticket', delivered.relative_path);
  expect(reopened.home_files('ticket')).toEqual([]);
});

test('real file-worker loss seals writes and a failed database close retains the main lock until retry', async () => {
  let worker: Worker | undefined;
  const post = Worker.prototype.postMessage;
  const observe = vi.spyOn(Worker.prototype, 'postMessage').mockImplementation(function (
    this: Worker,
    ...args: Parameters<Worker['postMessage']>
  ) {
    worker ??= this;
    return post.call(this, ...args);
  });
  const { spool, store, on_failure } = await fixture();
  observe.mockRestore();
  store.append(columns('before_loss'), payload, () => {});
  expect(worker).toBeDefined();
  const exited = new Promise<void>((resolve) => worker!.once('exit', () => resolve()));
  worker!.postMessage({ operation: 'stop' }); // Unexpected real exit, with the worker's own descriptor cleanup.
  await exited;
  expect(() => store.append(columns('after_loss'), payload, () => {})).toThrow();
  expect(on_failure).toHaveBeenCalledTimes(1);
  expect(() =>
    store.acknowledge('before_loss', store.sender_id, Buffer.from('{}'), () => {}),
  ).toThrow();
  await expect(OutboxStore.open(spool, clock, deadline(), () => {})).rejects.toThrow(
    'outbox is already owned',
  );
  const close = BetterSqlite3.prototype.close;
  let fail = true;
  const closing = vi.spyOn(BetterSqlite3.prototype, 'close').mockImplementation(function (
    this: BetterSqlite3.Database,
  ) {
    if (fail && !this.readonly && this.name === join(spool, DATABASE_NAME)) {
      fail = false;
      throw new Error('database close failed');
    }
    return close.call(this);
  });
  await expect(store.stop(deadline())).rejects.toThrow('database close failed');
  await expect(OutboxStore.open(spool, clock, deadline(), () => {})).rejects.toThrow(
    'outbox is already owned',
  );
  closing.mockRestore();
  await retained_failure(store.stop(deadline()), 'database close failed');
  await actual_failure(store, 'database close failed');
  const reopened = await OutboxStore.open(spool, clock, deadline(), () => {});
  stores.push(reopened);
  expect(reopened.pending().map((row) => row.submission_id)).toEqual(['before_loss']);
});
