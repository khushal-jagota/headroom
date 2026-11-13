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
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import { FilesService } from '../files.ts';
import { MAX_ENVELOPE_BYTES } from '../configuration.ts';
import { acquire_process_lock } from '../platform.ts';
import { DATABASE_NAME } from './schema.ts';
import { OutboxStore } from './outbox.ts';
import type { CaptureRows, ItemColumns, ItemFileColumns, TicketFileRow } from './contracts.ts';
import type { OutboxCleanup } from './resources.ts';

const roots: string[] = [],
  stores: OutboxCleanup[] = [];
const clock = {
  monotonic: () => performance.now() / 1000,
  now_ns: () => BigInt(Date.now()) * 1_000_000n,
};
const deadline = () => ({ deadline: performance.now() / 1000 + 10 });
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
  const store = await OutboxStore.open(spool, clock, deadline());
  stores.push(store);
  return { root, spool, artifacts, store };
}
afterEach(async () => {
  vi.restoreAllMocks();
  for (const store of stores.splice(0).reverse()) {
    await store.stop(deadline());
    await store.settled();
  }
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

test('an incompatible version-zero store refuses before a lock, WAL, or byte mutation', async () => {
  const spool = join(scratch(), 'spool');
  mkdirSync(spool);
  const path = join(spool, DATABASE_NAME);
  writeFileSync(path, '');
  const before = readFileSync(path);
  await expect(OutboxStore.open(spool, clock, deadline())).rejects.toThrow(
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
  const failure = await OutboxStore.open(spool, clock, deadline()).then(
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
  await cleanup.stop(deadline());
  await cleanup.settled();
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
    byte_count: 1n,
    modified_ns: 1n,
  };
  const large = Buffer.from(
    JSON.stringify({ payload: { text: 'x'.repeat(MAX_ENVELOPE_BYTES - 100) }, files: [] }),
  );
  expect(large.byteLength).toBeLessThan(MAX_ENVELOPE_BYTES);
  expect(() => store.append(columns('oversize'), large, [index], [index], () => {})).toThrow(
    'value exceeds its encoded size limit',
  );
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
  expect(store.append(columns('accepted'), payload, [], [], () => {}).sequence).toBe(1n);
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
  expect(warning).toHaveBeenCalledTimes(1);
  cleanup.mockRejectedValueOnce(new Error('receipt sweep failed'));
  const receipt = Buffer.from('opaque committed receipt');
  await expect(
    store.acknowledge('write', store.sender_id, receipt, true, [], () => {
      expect(store.lookup('write')?.receipt_json).toEqual(receipt);
    }),
  ).rejects.toThrow('receipt sweep failed');
  expect(warning).toHaveBeenCalledTimes(2);
  cleanup.mockRestore();
  await store.stop(deadline());
  await store.settled();
  expect(warning).toHaveBeenCalledTimes(2);
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
    expect(store.append(columns('other'), payload, [], [], () => {}).sequence).toBe(1n);
    await expect(store.stop({ deadline: performance.now() / 1000 - 1 })).rejects.toBeInstanceOf(
      ServerLifecycleError,
    );
    expect(() => store.append(columns('too_late'), payload, [], [], () => {})).toThrow();
    expect(() => store.with_capture('ticket', () => null)).toThrow();
    release.resolve();
    expect((await active).sequence).toBe(2n);
    expect(() => store.append(columns('still_late'), payload, [], [], () => {})).toThrow();
    await failed_stop.promise;
    await store.stop(deadline());
    await store.settled();
    expect(stop_attempts).toBe(2);
    stop.mockRestore();
    capture.mockRestore();
    const reopened = await OutboxStore.open(spool, clock, deadline());
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
  const { spool, store } = await fixture();
  observe.mockRestore();
  store.append(columns('before_loss'), payload, [], [], () => {});
  expect(worker).toBeDefined();
  const exited = new Promise<void>((resolve) => worker!.once('exit', () => resolve()));
  worker!.postMessage({ operation: 'stop' }); // Unexpected real exit, with the worker's own descriptor cleanup.
  await exited;
  expect(() => store.append(columns('after_loss'), payload, [], [], () => {})).toThrow();
  expect(() =>
    store.acknowledge('before_loss', store.sender_id, Buffer.from('{}'), true, [], () => {}),
  ).toThrow();
  expect(() => store.with_capture('ticket', () => null)).toThrow();
  await expect(OutboxStore.open(spool, clock, deadline())).rejects.toThrow(
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
  await expect(OutboxStore.open(spool, clock, deadline())).rejects.toThrow(
    'outbox is already owned',
  );
  closing.mockRestore();
  await store.stop(deadline());
  await store.settled();
  const reopened = await OutboxStore.open(spool, clock, deadline());
  stores.push(reopened);
  expect(reopened.pending().map((row) => row.submission_id)).toEqual(['before_loss']);
});
