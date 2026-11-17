/** Unique storage risks; populated parity and process-crash checks live in the direct driver. */
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Worker } from 'node:worker_threads';
import BetterSqlite3 from 'better-sqlite3';
import { afterEach, expect, test, vi } from 'vitest';
import { createReadAccess } from '../../../core/database/index.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { FilesService } from '../files.ts';
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
import type { CaptureRows, ItemColumns, ItemFileColumns, TicketFileRow } from './contracts.ts';
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
function captured(rows: CaptureRows): [Buffer, ItemFileColumns[], TicketFileRow[]] {
  return [
    Buffer.from(
      JSON.stringify({
        payload: {},
        files: rows.files.map((file) => ({
          scope: 'ticket',
          owner_id: file.ticket_id,
          relative_path: file.relative_path,
          sha256: file.sha256,
          byte_count: Number(file.byte_count),
        })),
      }),
    ),
    [...rows.files],
    [...rows.updates],
  ];
}
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
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

test('the full-item limit rolls back row and index allocation even with a no-op validator', async () => {
  const { spool, store } = await fixture();
  const index = {
    ticket_id: 'ticket',
    relative_path: 'file',
    sha256: 'a'.repeat(64),
    byte_count: 1,
    modified_ns: 1n,
  };
  const large = Buffer.from(
    JSON.stringify({ payload: { text: 'x'.repeat(MAX_ENVELOPE_BYTES - 100) }, files: [] }),
  );
  expect(large.byteLength).toBeLessThan(MAX_ENVELOPE_BYTES);
  let refusal: unknown;
  try {
    store.append(columns('oversize'), large, [index], [index], () => {});
  } catch (error) {
    refusal = error;
  }
  expect(refusal).toBeInstanceOf(ProtocolRefused);
  expect(refusal).toBeInstanceOf(ExecutorValidationError);
  expect(refusal).toMatchObject({ message: 'value exceeds its encoded size limit' });
  expect(store.lookup('oversize')).toBeNull();
  const read = createReadAccess(join(spool, DATABASE_NAME));
  try {
    expect(
      read.read((sql) =>
        sql.get(
          'SELECT (SELECT COUNT(*) FROM item_files) AS files, (SELECT COUNT(*) FROM ticket_files) AS indexed',
        ),
      ),
    ).toEqual({ files: 0, indexed: 0 });
  } finally {
    read.close();
  }
  expect(store.append(columns('accepted'), payload, [], [], () => {}).sequence).toBe(1);
});

test('committed capture and receipt results survive their distinct cleanup failures', async () => {
  const { artifacts, store } = await fixture();
  writeFileSync(join(artifacts, 'file'), 'durable authored bytes');
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const cleanup = vi
    .spyOn(FilesService.prototype, 'sweep_directory')
    .mockRejectedValueOnce(new Error('capture sweep failed'));
  const row = await store.with_capture('ticket', (rows) =>
    store.append(columns('write'), ...captured(rows), () => {}),
  );
  expect(store.lookup('write')?.sequence).toBe(row.sequence);
  await vi.waitFor(() => expect(warning).toHaveBeenCalledTimes(1));
  cleanup.mockRejectedValueOnce(new Error('receipt sweep failed'));
  const receipt = Buffer.from('opaque committed receipt');
  await store.acknowledge('write', store.sender_id, receipt, true, [], () => {
    expect(store.lookup('write')?.receipt_json).toEqual(receipt);
  });
  await vi.waitFor(() => expect(warning).toHaveBeenCalledTimes(2));
  expect(warning).toHaveBeenNthCalledWith(
    1,
    'Mac outbox cleanup deferred:',
    'Error: capture sweep failed',
  );
  expect(warning).toHaveBeenNthCalledWith(
    2,
    'Mac outbox cleanup deferred:',
    'Error: receipt sweep failed',
  );
  cleanup.mockRestore();
  await store.stop(deadline());
  await store.settled();
  expect(warning).toHaveBeenCalledTimes(2);
});

test('owned cleanup cannot delay committed receipts, local failures, or the next capture', async () => {
  const { store, artifacts } = await fixture();
  writeFileSync(join(artifacts, 'file'), 'durable authored bytes');
  const entered = deferred(),
    release = deferred();
  const cleanup = vi
    .spyOn(FilesService.prototype, 'sweep_directory')
    .mockImplementationOnce(async () => {
      entered.resolve();
      await release.promise;
    });
  await store.with_capture('ticket', (rows) =>
    store.append(columns('first'), ...captured(rows), () => {}),
  );
  await entered.promise;
  await store.acknowledge('first', store.sender_id, Buffer.from('{}'), true, [], () => {});
  store.append({ ...columns('failed'), kind: 'backend_event' }, payload, [], [], () => {});
  await store.fail_delivery('failed', Buffer.from('{}'), 'deadline expired', () => {});
  await store.with_capture('ticket', (rows) =>
    store.append(columns('next'), ...captured(rows), () => {}),
  );
  expect(store.lookup('first')?.receipt_json).not.toBeNull();
  expect(store.lookup('failed')?.receipt_json).not.toBeNull();
  expect(store.lookup('next')).not.toBeNull();
  expect(cleanup).toHaveBeenCalledTimes(1);
  let stopped = false;
  const stopping = store.stop(deadline()).then(() => {
    stopped = true;
  });
  await Promise.resolve();
  await Promise.resolve();
  expect(stopped).toBe(false);
  release.resolve();
  await stopping;
  expect(stopped).toBe(true);
});

test('a restart retains the delivery deadline and a failed backend event after child cleanup', async () => {
  const { spool, artifacts, store } = await fixture();
  const wall = vi.spyOn(clock, 'now_ns').mockReturnValue(1_700_000_000_000_000_000n);
  writeFileSync(join(artifacts, 'recoverable.txt'), 'retain failed file content');
  const original = await store.with_capture('ticket', (rows) => {
    const [encoded, files, updates] = captured(rows);
    const wrapper = JSON.parse(encoded.toString());
    wrapper.payload = { message: 'preserve this agent message' };
    return store.append(
      { ...columns('failed'), kind: 'backend_event' },
      Buffer.from(JSON.stringify(wrapper)),
      files,
      updates,
      () => {},
    );
  });
  const digest = JSON.parse(original.payload_json.toString()).files[0].sha256;
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
  resumed.append({ ...columns('stop'), kind: 'child_stopped' }, payload, [], [], () => {});
  await resumed.acknowledge('stop', resumed.sender_id, Buffer.from('{}'), true, [], () => {});
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
  expect(readFileSync(join(spool, 'blobs', digest), 'utf8')).toBe('retain failed file content');
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

test('partial file refusal preserves the event and other file through acceptance and restart', async () => {
  const { spool, artifacts, store } = await fixture();
  writeFileSync(join(artifacts, 'missing.txt'), 'unavailable content');
  writeFileSync(join(artifacts, 'good.txt'), 'keep this content');
  const row = await store.with_capture('ticket', (rows) => {
    const [encoded, files, updates] = captured(rows);
    const wrapper = JSON.parse(encoded.toString());
    wrapper.payload = { message: 'deliver this agent event' };
    return store.append(
      { ...columns('partial'), kind: 'backend_event' },
      Buffer.from(JSON.stringify(wrapper)),
      files,
      updates,
      () => {},
    );
  });
  const missing = JSON.parse(row.payload_json.toString()).files.find(
    (file: { relative_path: string }) => file.relative_path === 'missing.txt',
  );
  await store.refuse_file(
    'partial',
    {
      ticket_id: 'ticket',
      relative_path: missing.relative_path,
      sha256: missing.sha256,
    },
    'missing.txt: local blob is absent',
  );
  const partial = store.lookup('partial')!;
  const wrapper = JSON.parse(partial.payload_json.toString());
  expect(wrapper.payload).toEqual({ message: 'deliver this agent event' });
  expect(wrapper.files.map((file: { relative_path: string }) => file.relative_path)).toEqual([
    'good.txt',
  ]);
  const read = createReadAccess(join(spool, DATABASE_NAME));
  try {
    expect(read.read((sql) => sql.all('SELECT relative_path FROM item_files'))).toEqual([
      { relative_path: 'good.txt' },
    ]);
    expect(read.read((sql) => sql.all('SELECT relative_path FROM ticket_files'))).toEqual([
      { relative_path: 'good.txt' },
    ]);
  } finally {
    read.close();
  }
  await store.acknowledge('partial', store.sender_id, Buffer.from('{}'), true, [], () => {});
  store.append({ ...columns('stop'), kind: 'child_stopped' }, payload, [], [], () => {});
  await store.acknowledge('stop', store.sender_id, Buffer.from('{}'), true, [], () => {});
  await store.stop(deadline());
  const reopened = await OutboxStore.open(spool, clock, deadline(), () => {});
  stores.push(reopened);
  expect(reopened.pending_count()).toBe(0);
  expect(delivery_state(reopened.lookup('partial')!).file_refusals).toEqual([
    {
      relative_path: 'missing.txt',
      sha256: missing.sha256,
      reason: 'missing.txt: local blob is absent',
    },
  ]);
  expect(reopened.delivery_status()).toEqual({
    failed_count: 0,
    refused_file_count: 1,
    last_error: 'missing.txt: local blob is absent',
  });
});

test('capture yields the writer and shutdown drains its reservation despite a failed inner stop', async () => {
  const { artifacts, spool, store } = await fixture();
  writeFileSync(join(artifacts, 'file'), 'admitted capture bytes');
  const entered = deferred(),
    release = deferred(),
    failed_stop = deferred();
  const original_stop = FilesService.prototype.stop;
  let stop_attempts = 0;
  const stop = vi.spyOn(FilesService.prototype, 'stop').mockImplementation(function (
    this: FilesService,
    ...args: Parameters<FilesService['stop']>
  ) {
    if (++stop_attempts === 1) {
      failed_stop.resolve();
      return Promise.reject(new Error('stop frame failed'));
    }
    return original_stop.call(this, ...args);
  });
  const original = FilesService.prototype.capture_file;
  const capture = vi
    .spyOn(FilesService.prototype, 'capture_file')
    .mockImplementation(async function (
      this: FilesService,
      ...args: Parameters<FilesService['capture_file']>
    ) {
      entered.resolve();
      await release.promise;
      return original.call(this, ...args);
    });
  const active = store.with_capture('ticket', (rows) =>
    store.append(columns('captured'), ...captured(rows), () => {}),
  );
  void active.catch(() => {});
  try {
    await entered.promise;
    expect(store.append(columns('other'), payload, [], [], () => {}).sequence).toBe(1);
    await expect(store.stop({ deadline: performance.now() / 1000 - 1 })).rejects.toBeInstanceOf(
      ServerLifecycleError,
    );
    expect(() => store.append(columns('too_late'), payload, [], [], () => {})).toThrow();
    expect(() => store.with_capture('ticket', () => null)).toThrow();
    release.resolve();
    expect((await active).sequence).toBe(2);
    expect(() => store.append(columns('still_late'), payload, [], [], () => {})).toThrow();
    await failed_stop.promise;
    await retained_failure(store.stop(deadline()), 'stop frame failed');
    await actual_failure(store, 'stop frame failed');
    expect(stop_attempts).toBe(2);
    stop.mockRestore();
    capture.mockRestore();
    const reopened = await OutboxStore.open(spool, clock, deadline(), () => {});
    stores.push(reopened);
    expect(reopened.pending().map((row) => row.submission_id)).toEqual(['other', 'captured']);
  } finally {
    release.resolve();
  }
});

test('an artifact ancestor swap never captures the foreign sentinel', async () => {
  const { root, spool, artifacts, store } = await fixture();
  const directory = join(artifacts, 'nested'),
    foreign = join(root, 'foreign');
  mkdirSync(directory);
  mkdirSync(foreign);
  writeFileSync(join(directory, 'file'), 'original bytes');
  writeFileSync(join(foreign, 'file'), 'foreign sentinel bytes');
  symlinkSync(foreign, join(artifacts, 'foreign-link'), 'dir');
  const original = FilesService.prototype.capture_file;
  vi.spyOn(FilesService.prototype, 'capture_file').mockImplementation(async function (
    this: FilesService,
    ...args: Parameters<FilesService['capture_file']>
  ) {
    renameSync(directory, join(root, 'held-artifact'));
    symlinkSync(foreign, directory, 'dir');
    return original.call(this, ...args);
  });
  expect((await store.with_capture('ticket', (rows) => rows)).files).toEqual([]);
  expect(readFileSync(join(foreign, 'file'), 'utf8')).toBe('foreign sentinel bytes');
  expect(readdirSync(join(spool, 'blobs'))).toEqual([]);
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
  store.append(columns('before_loss'), payload, [], [], () => {});
  expect(worker).toBeDefined();
  const exited = new Promise<void>((resolve) => worker!.once('exit', () => resolve()));
  worker!.postMessage({ operation: 'stop' }); // Unexpected real exit, with the worker's own descriptor cleanup.
  await exited;
  expect(() => store.append(columns('after_loss'), payload, [], [], () => {})).toThrow();
  expect(on_failure).toHaveBeenCalledTimes(1);
  expect(() =>
    store.acknowledge('before_loss', store.sender_id, Buffer.from('{}'), true, [], () => {}),
  ).toThrow();
  expect(() => store.with_capture('ticket', () => null)).toThrow();
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
