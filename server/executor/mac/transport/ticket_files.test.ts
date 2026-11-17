/** Real spool rows and the real file worker. Only the two home requests are scripted. */
import { createHash } from 'node:crypto';
import {
  mkdirSync,
  mkdtempSync,
  realpathSync,
  renameSync,
  rmSync,
  symlinkSync,
  utimesSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import type { FileManifest } from '../../wire.generated.ts';
import { FilesService } from '../files.ts';
import { create_mac_output_publication } from '../runner/publication.ts';
import { OutboxStore } from '../storage/outbox.ts';
import { ExecutorHttpError } from './errors.ts';
import { MacTicketFileSync } from './ticket_files.ts';
import type { TicketFileSyncOptions } from './ticket_files.ts';

const stop_options = () => ({ deadline: performance.now() / 1000 + 10 });
const clock = {
  monotonic: () => performance.now() / 1000,
  now_ns: () => BigInt(Date.now()) * 1_000_000n,
};
const sha = (text: string) => createHash('sha256').update(text).digest('hex');
const cleanup: (() => Promise<void>)[] = [];
afterEach(async () => {
  vi.useRealTimers();
  for (const step of cleanup.splice(0).reverse()) await step();
});
async function fixture(transfer: Partial<TicketFileSyncOptions['transfer']> = {}) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'ticket-file-sync-')));
  const ticket = join(root, 'tickets', 'ticket');
  mkdirSync(join(ticket, 'artifacts'), { recursive: true });
  const store = await OutboxStore.open(join(root, 'spool'), clock, stop_options(), () => {});
  const files = new FilesService();
  const uploads: string[] = [],
    removals: string[] = [],
    warnings: string[] = [];
  const sync = new MacTicketFileSync({
    store,
    files,
    transfer: {
      async upload_ticket_file(manifest: FileManifest) {
        uploads.push(`${manifest.relative_path} ${manifest.sha256}`);
      },
      async remove_ticket_file(manifest: FileManifest) {
        removals.push(manifest.relative_path);
      },
      ...transfer,
    },
    tickets_root: join(root, 'tickets'),
    now_ns: clock.now_ns,
    warn: (message) => warnings.push(message),
  });
  cleanup.push(async () => {
    await sync.stop(stop_options());
    await files.stop(stop_options());
    await store.stop(stop_options());
    rmSync(root, { recursive: true, force: true });
  });
  /** A file old enough that its timestamp alone proves it unchanged. */
  const write = (path: string, text: string, age_seconds = 60) => {
    writeFileSync(join(ticket, path), text);
    const at = Date.now() / 1000 - age_seconds;
    utimesSync(join(ticket, path), at, at);
  };
  return { root, ticket, store, sync, uploads, removals, warnings, write };
}

test('each changed artifact goes once, a deleted one is removed, and other folders stay local', async () => {
  const f = await fixture();
  mkdirSync(join(f.ticket, 'artifacts/deep'));
  mkdirSync(join(f.ticket, 'build'));
  mkdirSync(join(f.root, 'foreign'));
  f.write('artifacts/a.txt', 'first');
  f.write('artifacts/deep/b.txt', 'nested');
  f.write('build/app.bin', 'a large build product');
  writeFileSync(join(f.root, 'foreign/secret'), 'outside the Ticket');
  symlinkSync(join(f.root, 'foreign'), join(f.ticket, 'artifacts/link'), 'dir');

  // A restart resumes: the start pass finds the Ticket folder without any new item.
  f.sync.start();
  await f.sync.settled();
  expect(f.uploads).toEqual([
    `artifacts/a.txt ${sha('first')}`,
    `artifacts/deep/b.txt ${sha('nested')}`,
  ]);

  f.store.note_ticket_activity('ticket');
  await f.sync.settled();
  expect(f.uploads).toHaveLength(2);

  f.write('artifacts/a.txt', 'second', 30);
  rmSync(join(f.ticket, 'artifacts/deep/b.txt'));
  f.store.note_ticket_activity('ticket');
  await f.sync.settled();
  expect(f.uploads.slice(2)).toEqual([`artifacts/a.txt ${sha('second')}`]);
  expect(f.removals).toEqual(['artifacts/deep/b.txt']);

  // A missing folder is not proof that its files were deleted.
  renameSync(join(f.ticket, 'artifacts'), join(f.ticket, 'moved'));
  f.store.note_ticket_activity('ticket');
  await f.sync.settled();
  expect(f.removals).toHaveLength(1);
  expect(f.store.home_files('ticket').map((row) => row.relative_path)).toEqual(['artifacts/a.txt']);
});

test('an item is queued while an upload still runs, and a failed or refused file delays nothing', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const attempts: string[] = [];
  const f = await fixture({
    async upload_ticket_file(manifest: FileManifest) {
      attempts.push(manifest.relative_path);
      if (manifest.relative_path === 'artifacts/refused.txt')
        throw new ExecutorHttpError(403, 'agent_forbidden', 'Ticket belongs to another machine');
      if (attempts.length === 1) await held;
      if (attempts.filter((path) => path === manifest.relative_path).length === 1)
        throw new Error('home is offline');
    },
  });
  f.write('artifacts/large.bin', 'slow bytes');
  f.write('artifacts/refused.txt', 'never accepted');
  f.sync.start();
  const publication = create_mac_output_publication(f.store);
  const add = (submission_id: string) =>
    publication.add(
      { submission_id, conversation_id: 'conversation', kind: 'backend_event', payload: {} },
      { ticket_id: 'ticket' },
    );
  await vi.waitFor(() => expect(attempts).toEqual(['artifacts/large.bin']));
  await add('tool_result');
  await add('turn_end');
  expect(f.store.pending().map((row) => row.submission_id)).toEqual(['tool_result', 'turn_end']);
  expect(attempts).toHaveLength(1);

  release();
  await vi.waitFor(
    () =>
      expect(f.store.home_files('ticket').map((row) => row.relative_path)).toEqual([
        'artifacts/large.bin',
      ]),
    { timeout: 5000 },
  );
  await f.sync.settled();
  // One retry for the offline failure. The refusal is reported and not retried by its own pass.
  expect(attempts.filter((path) => path === 'artifacts/large.bin')).toHaveLength(2);
  expect(f.warnings.some((line) => line.includes('home is offline'))).toBe(true);
  expect(f.warnings.some((line) => line.includes('artifacts/refused.txt: HTTP 403'))).toBe(true);
});
