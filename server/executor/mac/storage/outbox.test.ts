/** Internal storage proofs. Wire codecs, send loops, and crash parity belong to their own checks. */
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
import { afterEach, expect, test, vi } from 'vitest';
import { FilesService } from '../files.ts';
import { DATABASE_NAME } from './schema.ts';
import { OutboxStore } from './outbox.ts';
import type {
  CaptureRows,
  ItemColumns,
  ItemFileColumns,
  ItemRow,
  TicketFileRow,
} from './contracts.ts';

const roots: string[] = [],
  stores: OutboxStore[] = [];
const clock = {
  monotonic: () => performance.now() / 1000,
  now_ns: () => BigInt(Date.now()) * 1_000_000n,
};
const payload = Buffer.from('opaque storage bytes');
function columns(submission_id: string): ItemColumns {
  return { submission_id, conversation_id: 'conversation', child_id: 'child', kind: 'cli_write' };
}
function item_files(rows: CaptureRows): ItemFileColumns[] {
  return rows.files.map(({ relative_path, sha256, byte_count }) => ({
    relative_path,
    sha256,
    byte_count,
  }));
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
  const root = scratch();
  const spool = join(root, 'spool'),
    artifacts = join(root, 'artifacts', 'tickets', 'ticket');
  mkdirSync(artifacts, { recursive: true });
  const store = await OutboxStore.open(spool, clock);
  stores.push(store);
  return { root, spool, artifacts, store };
}
afterEach(async () => {
  vi.restoreAllMocks();
  for (const store of stores.splice(0).reverse()) {
    await store.stop({ deadline: performance.now() / 1000 + 10 });
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
  await expect(OutboxStore.open(spool, clock)).rejects.toThrow('unsupported outbox format');
  expect(readFileSync(path)).toEqual(before);
  expect(readdirSync(spool)).toEqual([DATABASE_NAME]);
});

test('the mandatory precommit validator can refuse an allocated row without durable publication', async () => {
  const { store } = await fixture();
  const index: TicketFileRow = {
    ticket_id: 'ticket',
    relative_path: 'file',
    sha256: 'a'.repeat(64),
    byte_count: 1n,
    modified_ns: 1n,
  };
  let validated: ItemRow | undefined;
  expect(() =>
    store.append(columns('refused'), payload, [index], [index], (row) => {
      validated = row;
      throw new Error('encoded submission exceeds wire budget');
    }),
  ).toThrow('encoded submission exceeds wire budget');
  expect(validated?.sequence).toBe(1n);
  expect(store.lookup('refused')).toBeNull();
  expect(store.pending()).toEqual([]);
  const accepted = store.append(columns('accepted'), payload, [], [], () => {});
  expect(accepted.sequence).toBe(1n);
  expect(accepted.payload_json).toEqual(payload);
  expect(store.pending()).toEqual([accepted]);
});

test('receipt publication observes its committed row before a later blob-cleanup failure', async () => {
  const { artifacts, store } = await fixture();
  writeFileSync(join(artifacts, 'file'), 'durable authored bytes');
  const row = await store.with_capture('ticket', (captured) =>
    store.append(columns('write'), payload, item_files(captured), [...captured.updates], () => {}),
  );
  const receipt = Buffer.from('opaque committed receipt');
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const cleanup = vi
    .spyOn(FilesService.prototype, 'sweep_directory')
    .mockRejectedValueOnce(new Error('cleanup failed'));
  let committed = false;
  await expect(
    store.acknowledge('write', store.sender_id, receipt, true, [], () => {
      expect(store.lookup('write')?.receipt_json).toEqual(receipt);
      committed = true;
    }),
  ).rejects.toThrow('cleanup failed');
  expect(committed).toBe(true);
  expect(store.lookup('write')?.sequence).toBe(row.sequence);
  expect(store.pending()).toEqual([]);
  expect(warning).toHaveBeenCalledTimes(1);
  cleanup.mockRestore();
  await store.stop({ deadline: performance.now() / 1000 + 10 });
  await store.settled();
  expect(warning).toHaveBeenCalledTimes(1);
});

test('capture yields the writer and shutdown drains its reserved callback despite a failed inner stop', async () => {
  const { artifacts, spool, store } = await fixture();
  writeFileSync(join(artifacts, 'file'), 'admitted capture bytes');
  const entered = deferred(),
    release = deferred();
  const failed_stop = deferred();
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
  const active = store.with_capture('ticket', (captured) =>
    store.append(
      columns('captured'),
      payload,
      item_files(captured),
      [...captured.updates],
      () => {},
    ),
  );
  // Retain a rejection handler while the test exercises shutdown before release.
  void active.catch(() => {});
  try {
    await entered.promise;
    const other = store.append(columns('other'), payload, [], [], () => {});
    expect(other.sequence).toBe(1n);
    await expect(store.stop({ deadline: performance.now() / 1000 - 1 })).rejects.toMatchObject({
      name: 'ServerLifecycleError',
    });
    expect(() => store.append(columns('too_late'), payload, [], [], () => {})).toThrow();
    expect(() => store.with_capture('ticket', () => null)).toThrow();
    release.resolve();
    expect((await active).sequence).toBe(2n);
    expect(() => store.append(columns('still_late'), payload, [], [], () => {})).toThrow();
    await failed_stop.promise;
    await store.stop({ deadline: performance.now() / 1000 + 10 });
    await store.settled();
    expect(stop_attempts).toBe(2);
    stop.mockRestore();
    capture.mockRestore();
    const reopened = await OutboxStore.open(spool, clock);
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
  const capture = vi
    .spyOn(FilesService.prototype, 'capture_file')
    .mockImplementation(async function (
      this: FilesService,
      ...args: Parameters<FilesService['capture_file']>
    ) {
      renameSync(directory, join(root, 'held-artifact'));
      symlinkSync(foreign, directory, 'dir');
      return original.call(this, ...args);
    });
  const captured = await store.with_capture('ticket', (rows) => rows);
  expect(captured.files).toEqual([]);
  expect(captured.updates).toEqual([]);
  expect(readFileSync(join(foreign, 'file'), 'utf8')).toBe('foreign sentinel bytes');
  expect(readdirSync(foreign)).toEqual(['file']);
  expect(readdirSync(join(spool, 'blobs'))).toEqual([]);
  expect(capture).toHaveBeenCalledTimes(1);
});
