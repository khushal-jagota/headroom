/** Durable local rows and immutable blobs. Wire codecs and send loops belong to the host. */
import type { BigIntStats } from 'node:fs';
import { readdir, lstat, opendir } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { randomUUID } from 'node:crypto';
import {
  assertDatabaseVersion,
  createDurableSidecarDatabase,
} from '../../../core/database/index.ts';
import type { Database } from '../../../core/database/contracts.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { FilesService, contained_path, safe_component } from '../files.ts';
import type { DirectoryHandle } from '../files.ts';
import { error_code, file_error } from '../file_metadata.ts';
import { acquire_process_lock } from '../platform.ts';
import { MAX_ITEM_BATCH_ITEMS, MAX_LEASE_WAITING_CONVERSATIONS } from '../configuration.ts';
import { OutboxResources } from './resources.ts';
import { item_size_guard } from './size.ts';
import { DATABASE_NAME, FORMAT_REFUSAL, OUTBOX_SCHEMA, SPOOL_FORMAT_VERSION } from './schema.ts';
import type {
  CaptureRows,
  ItemColumns,
  ItemFileColumns,
  ItemRow,
  OutboxClock,
  TicketFileKey,
  TicketFileRow,
} from './contracts.ts';
export type * from './contracts.ts';
export type { OutboxCleanup } from './resources.ts';

async function exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if (['ENOENT', 'ENOTDIR'].includes(error_code(error) ?? '')) return false;
    throw error;
  }
}

function synchronous<T>(value: T): T {
  if (
    value !== null &&
    (typeof value === 'object' || typeof value === 'function') &&
    typeof (value as { then?: unknown }).then === 'function'
  ) {
    if (value instanceof Promise) void value.catch(() => {});
    throw new TypeError('outbox callbacks must be synchronous');
  }
  return value;
}
function unreadable(error: unknown): boolean {
  return (
    ['ENOENT', 'ENOTDIR', 'EACCES', 'EPERM'].includes(error_code(error) ?? '') ||
    (error as Error).name === 'ValueError'
  );
}
export class OutboxStore {
  readonly sender_id: string;
  readonly root: string;
  private readonly database: Database;
  private readonly files: FilesService;
  private readonly blobs: DirectoryHandle;
  private readonly clock: OutboxClock;
  private readonly resources: OutboxResources;
  private closed = false;
  private callback_scope = 0;
  private capturing = 0;
  private readonly first_held = new Map<bigint, number>();
  private readonly capture_tails = new Map<string, Promise<void>>();
  private readonly operations = new Set<Promise<unknown>>();
  private drained: Promise<void> | undefined;
  private cleanup: Promise<void> | undefined;
  private constructor(
    root: string,
    database: Database,
    files: FilesService,
    blobs: DirectoryHandle,
    clock: OutboxClock,
    resources: OutboxResources,
  ) {
    this.root = root;
    this.database = database;
    this.files = files;
    this.blobs = blobs;
    this.clock = clock;
    this.resources = resources;
    files.on_failure(() => {
      this.closed = true;
    });
    const sender = database.read((sql) =>
      sql.get<{ sender_id: string }>('SELECT sender_id FROM sender WHERE singleton=1'),
    );
    if (sender === undefined) throw new Error('outbox sender is absent');
    this.sender_id = sender.sender_id;
  }
  static async open(root: string, clock: OutboxClock, options: StopOptions): Promise<OutboxStore> {
    const path = join(root, DATABASE_NAME);
    const existed = assertDatabaseVersion(path, SPOOL_FORMAT_VERSION, FORMAT_REFUSAL);
    if (!existed && (await exists(root)) && (await readdir(root)).some((name) => name !== 'lock'))
      throw file_error(FORMAT_REFUSAL);
    const files = new FilesService();
    const resources = new OutboxResources(files);
    let database: Database | undefined;
    try {
      await files.open_directory(root, { create: true });
      files.assert_healthy();
      resources.lock = acquire_process_lock(root);
      files.assert_healthy();
      database = createDurableSidecarDatabase(path, {
        schemaVersion: SPOOL_FORMAT_VERSION,
        foreignKeys: true,
        refusalMessage: FORMAT_REFUSAL,
        initialize(tx) {
          for (const statement of OUTBOX_SCHEMA) tx.run(statement);
          tx.run('INSERT INTO sender VALUES(1,?)', [randomUUID().replaceAll('-', '')]);
          files.assert_healthy();
        },
      });
      resources.database = database;
      if (!existed) await files.fsync_directory(root);
      const blobs = await files.open_directory(join(root, 'blobs'), { create: true });
      await files.fsync_directory(root);
      const store = new OutboxStore(root, database, files, blobs, clock, resources);
      files.assert_healthy();
      database.write((tx) => {
        tx.run('DELETE FROM items WHERE receipt_json IS NOT NULL');
        files.assert_healthy();
      });
      const opened = clock.monotonic();
      const sequences = database.read((sql) =>
        sql.all<{ sequence: bigint }>(
          'SELECT sequence FROM items WHERE receipt_json IS NULL ORDER BY sequence',
        ),
      );
      for (const row of sequences) store.first_held.set(row.sequence, opened);
      await store.sweep();
      return store;
    } catch (error) {
      const failure = error instanceof Error ? error : new Error(String(error));
      try {
        await resources.stop(options);
      } catch (cleanup_error) {
        const outward =
          cleanup_error instanceof Error ? cleanup_error : new Error(String(cleanup_error));
        Object.assign(outward, { cause: failure });
        if (!resources.is_settled) Object.assign(outward, { cleanup: resources });
        throw outward;
      }
      throw failure;
    }
  }
  private admit(capture_append = false): void {
    this.files.assert_healthy();
    if (this.closed && (!capture_append || this.callback_scope === 0))
      throw new ServerLifecycleError('outbox is closed');
  }
  private track<T>(task: Promise<T>): Promise<T> {
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      (error) => {
        this.operations.delete(task);
        console.warn('Mac outbox operation failed:', error);
      },
    );
    return task;
  }
  lookup(submission_id: string): ItemRow | null {
    this.admit();
    return this.held(submission_id);
  }
  private held(submission_id: string): ItemRow | null {
    return this.database.read(
      (sql) =>
        sql.get<ItemRow>('SELECT * FROM items WHERE submission_id=?', [submission_id]) ?? null,
    );
  }
  pending(conversation_id?: string): ItemRow[] {
    this.admit();
    return this.database.read((sql) =>
      conversation_id === undefined
        ? sql.all<ItemRow>(
            'SELECT * FROM items WHERE receipt_json IS NULL ORDER BY sequence LIMIT ?',
            [MAX_ITEM_BATCH_ITEMS],
          )
        : sql.all<ItemRow>(
            'SELECT * FROM items WHERE receipt_json IS NULL AND conversation_id=? ORDER BY sequence LIMIT ?',
            [safe_component(conversation_id), MAX_ITEM_BATCH_ITEMS],
          ),
    );
  }
  pending_conversations(): readonly (readonly [string, bigint])[] {
    this.admit();
    return this.database.read((sql) =>
      sql
        .all<{ conversation_id: string; oldest: bigint }>(
          'SELECT conversation_id,MIN(sequence) AS oldest FROM items WHERE receipt_json IS NULL GROUP BY conversation_id ORDER BY MIN(sequence) LIMIT ?',
          [MAX_LEASE_WAITING_CONVERSATIONS + 1],
        )
        .map((row) => [row.conversation_id, row.oldest] as const),
    );
  }
  pending_count(): bigint {
    this.admit();
    return this.database.read(
      (sql) =>
        sql.get<{ count: bigint }>(
          'SELECT COUNT(*) AS count FROM items WHERE receipt_json IS NULL',
        )!.count,
    );
  }
  oldest_age_ms(): number | null {
    this.admit();
    const first = this.database.read((sql) =>
      sql.get<{ sequence: bigint }>(
        'SELECT sequence FROM items WHERE receipt_json IS NULL ORDER BY sequence LIMIT 1',
      ),
    );
    const held = first === undefined ? undefined : this.first_held.get(first.sequence);
    return held === undefined ? null : Math.trunc((this.clock.monotonic() - held) * 1000);
  }
  append(
    columns: ItemColumns,
    payload: Buffer,
    files: readonly ItemFileColumns[],
    updates: readonly TicketFileRow[],
    validate: (row: ItemRow) => void,
  ): ItemRow {
    this.admit(true);
    if (columns.submission_id !== undefined) {
      const prior = this.held(safe_component(columns.submission_id));
      if (prior !== null) return prior;
    }
    const submission = safe_component(columns.submission_id || randomUUID().replaceAll('-', ''));
    safe_component(columns.conversation_id);
    const size_guard = item_size_guard(this.sender_id, columns, submission, payload);
    this.files.assert_healthy();
    const row = this.database.write((tx) => {
      const result = tx.run(
        'INSERT INTO items(submission_id,conversation_id,child_id,kind,payload_json) VALUES(?,?,?,?,?)',
        [submission, columns.conversation_id, columns.child_id, columns.kind, payload],
      );
      const sequence = BigInt(result.lastInsertRowid);
      size_guard(sequence);
      for (const file of files)
        tx.run('INSERT INTO item_files VALUES(?,?,?,?)', [
          sequence,
          file.relative_path,
          file.sha256,
          file.byte_count,
        ]);
      for (const file of updates)
        tx.run(
          'INSERT INTO ticket_files VALUES(?,?,?,?,?) ON CONFLICT(ticket_id,relative_path) DO UPDATE SET byte_count=excluded.byte_count,modified_ns=excluded.modified_ns,sha256=excluded.sha256',
          [file.ticket_id, file.relative_path, file.byte_count, file.modified_ns, file.sha256],
        );
      const held = tx.get<ItemRow>('SELECT * FROM items WHERE sequence=?', [sequence])!;
      synchronous(validate(held));
      this.files.assert_healthy();
      return held;
    });
    this.first_held.set(row.sequence, this.clock.monotonic());
    return row;
  }
  with_capture<T>(ticket_id: string, callback: (capture: CaptureRows) => T): Promise<T> {
    this.admit();
    safe_component(ticket_id);
    const previous = this.capture_tails.get(ticket_id) ?? Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tail = previous.then(() => gate);
    this.capture_tails.set(ticket_id, tail);
    const task = (async () => {
      await previous;
      this.capturing++;
      try {
        const captured = await this.capture(ticket_id);
        this.callback_scope++;
        try {
          return synchronous(callback(captured));
        } finally {
          this.callback_scope--;
        }
      } finally {
        this.capturing--;
        try {
          await this.sweep();
        } catch (error) {
          // Durable append success survives cleanup failure. Retry on a later sweep.
          console.warn('Mac outbox capture cleanup deferred:', error);
        } finally {
          release();
          if (this.capture_tails.get(ticket_id) === tail) this.capture_tails.delete(ticket_id);
        }
      }
    })();
    return this.track(task);
  }
  private async capture(ticket_id: string): Promise<CaptureRows> {
    const root = join(dirname(this.root), 'artifacts', 'tickets', ticket_id);
    const files: TicketFileRow[] = [],
      updates: TicketFileRow[] = [];
    const checked_ns = this.clock.now_ns();
    const folders = (await exists(root)) ? [root] : [];
    while (folders.length) {
      const folder = folders.pop()!;
      let entries: string[];
      let held: DirectoryHandle | undefined;
      try {
        held = await this.files.open_directory(folder);
        entries = (await readdir(folder)).sort((left, right) =>
          Buffer.compare(Buffer.from(left), Buffer.from(right)),
        );
      } catch (error) {
        if (!unreadable(error)) throw error;
        console.warn(`Skipping unreadable artifact directory: ${folder}`);
        continue;
      } finally {
        if (held !== undefined) await this.files.close_directory(held);
      }
      for (const name of entries) {
        const relative_path = relative(root, join(folder, name));
        let source: string, info: BigIntStats;
        try {
          source = contained_path(root, relative_path);
          info = await lstat(source, { bigint: true });
          if (!info.isDirectory() && !info.isFile())
            throw file_error('artifact is not regular file');
        } catch (error) {
          if (!unreadable(error)) throw error;
          console.warn(`Skipping unreadable artifact: ${relative_path}`);
          continue;
        }
        if (info.isDirectory()) {
          folders.push(source);
          continue;
        }
        const previous = this.database.read((sql) => {
          const row = sql.get<TicketFileRow>(
            'SELECT * FROM ticket_files WHERE ticket_id=? AND relative_path=?',
            [ticket_id, relative_path],
          );
          if (row === undefined || row.byte_count !== info.size || row.modified_ns !== info.mtimeNs)
            return undefined;
          const waiting = sql.get(
            'SELECT 1 FROM item_files JOIN items USING(sequence) WHERE sha256=? AND receipt_json IS NULL LIMIT 1',
            [row.sha256],
          );
          return { row, waiting: waiting !== undefined };
        });
        if (previous !== undefined) {
          if (previous.waiting) files.push(previous.row);
          continue;
        }
        const captured = await this.files.capture_file(source, this.blobs, checked_ns);
        if (captured === null) {
          console.warn(`Skipping unreadable or changing artifact: ${relative_path}`);
          continue;
        }
        const row = { ticket_id, relative_path, ...captured };
        files.push(row);
        updates.push(row);
      }
    }
    return { files, updates };
  }
  acknowledge(
    submission_id: string,
    sender_id: string,
    receipt: Buffer,
    accepted: boolean,
    invalidated: readonly TicketFileKey[],
    confirmed: () => void,
  ): Promise<void> {
    this.admit();
    const task = (async () => {
      // Register the whole operation before a host callback can request shutdown.
      await Promise.resolve();
      const row = this.held(submission_id);
      if (row === null) return;
      if (sender_id !== this.sender_id) throw file_error('receipt identity mismatch');
      this.files.assert_healthy();
      this.database.write((tx) => {
        if (!accepted)
          for (const file of invalidated)
            tx.run('DELETE FROM ticket_files WHERE ticket_id=? AND relative_path=? AND sha256=?', [
              file.ticket_id,
              file.relative_path,
              file.sha256,
            ]);
        if (row.kind === 'cli_write')
          tx.run('UPDATE items SET receipt_json=? WHERE submission_id=?', [receipt, submission_id]);
        else tx.run('DELETE FROM items WHERE submission_id=?', [submission_id]);
        if (row.kind === 'child_stopped')
          tx.run('DELETE FROM items WHERE child_id=? AND receipt_json IS NOT NULL', [row.child_id]);
        this.files.assert_healthy();
      });
      this.first_held.delete(row.sequence);
      // The host can settle its waiter even when subsequent blob cleanup fails.
      synchronous(confirmed());
      await this.sweep();
    })();
    return this.track(task);
  }
  private async sweep(): Promise<void> {
    if (this.capturing) return;
    const directory = await opendir(join(this.root, 'blobs'));
    try {
      if ((await directory.read()) === null) return;
    } finally {
      await directory.close();
    }
    // A capture can start during the asynchronous empty-directory observation.
    if (this.capturing) return;
    const keep = this.database.read((sql) =>
      sql
        .all<{ sha256: string }>(
          'SELECT DISTINCT sha256 FROM item_files JOIN items USING(sequence) WHERE receipt_json IS NULL',
        )
        .map((row) => row.sha256),
    );
    await this.files.sweep_directory(this.blobs, join(this.root, 'blobs'), keep);
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.drained ??= (async () => {
      // An admitted synchronous append must leave its writer scope before close.
      await Promise.resolve();
      while (this.operations.size) await Promise.allSettled([...this.operations]);
    })();
    // Delegate every stop attempt after the admitted work drains. A failed stop-frame
    // send remains retryable without sealing the file service during capture.
    const attempt = this.drained.then(() => this.resources.stop(options));
    this.cleanup ??= this.drained.then(() => this.resources.settled());
    return observe_shutdown(
      Promise.all([attempt, this.cleanup]).then(() => undefined),
      options,
      'Outbox work remains unfinished at the shutdown deadline.',
    );
  }
  async settled(): Promise<void> {
    if (this.cleanup !== undefined) return this.cleanup;
    while (this.operations.size) await Promise.allSettled([...this.operations]);
    await this.resources.settled();
    if (this.cleanup !== undefined) await this.cleanup;
  }
}
