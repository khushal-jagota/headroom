import { counter } from '../../logic/counters.ts';
/** Durable local rows. Wire codecs and send loops belong to the host. */
import { readdir, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import {
  assertDatabaseVersion,
  createDurableSidecarDatabase,
} from '../../../core/database/index.ts';
import type { Database, DurableSidecarOpenFailure } from '../../../core/database/contracts.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { FilesService, safe_component } from '../files.ts';
import { error_code, file_error } from '../file_metadata.ts';
import { acquire_process_lock } from '../platform.ts';
import { failure_detail } from '../failure_detail.ts';
import { MAX_ITEM_BATCH_ITEMS, MAX_LEASE_WAITING_CONVERSATIONS } from '../configuration.ts';
import { OutboxResources } from './resources.ts';
import type { SidecarOpenScope } from './journal_contracts.ts';
import { item_size_guard } from './size.ts';
import { DATABASE_NAME, FORMAT_REFUSAL, OUTBOX_SCHEMA, SPOOL_FORMAT_VERSION } from './schema.ts';
import {
  delivery_diagnostics,
  delivery_state,
  NO_DELIVERY_DIAGNOSTICS,
  stored_payload,
} from './delivery.ts';
import type {
  ItemColumns,
  ItemRow,
  OutboxClock,
  OutboxDeliveryStatus,
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

function item_row(row: ItemRow): ItemRow {
  return { ...row, sequence: counter(row.sequence) };
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
export class OutboxStore {
  readonly pending_events = new EventEmitter();
  readonly health_fence = new SharedArrayBuffer(Int32Array.BYTES_PER_ELEMENT);
  readonly sender_id: string;
  readonly root: string;
  private readonly database: Database;
  private readonly files: FilesService;
  private readonly clock: OutboxClock;
  private readonly resources: OutboxResources;
  private readonly on_failure: (error: Error) => void;
  private faulted = false;
  private closed = false;
  private readonly first_held = new Map<number, number>();
  private readonly operations = new Set<Promise<unknown>>();
  private drained: Promise<void> | undefined;
  private cleanup: Promise<void> | undefined;
  private constructor(
    root: string,
    database: Database,
    files: FilesService,
    clock: OutboxClock,
    resources: OutboxResources,
    on_failure: (error: Error) => void,
  ) {
    this.root = root;
    this.database = database;
    this.files = files;
    this.clock = clock;
    this.resources = resources;
    this.on_failure = on_failure;
    files.on_failure((error) => this.failed(error));
    const sender = database.read((sql) =>
      sql.get<{ sender_id: string }>('SELECT sender_id FROM sender WHERE singleton=1'),
    );
    if (sender === undefined) throw new Error('outbox sender is absent');
    this.sender_id = sender.sender_id;
  }
  static async open(
    root: string,
    clock: OutboxClock,
    options: StopOptions,
    on_failure: (error: Error) => void,
  ): Promise<OutboxStore> {
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
      const store = new OutboxStore(root, database, files, clock, resources, on_failure);
      files.assert_healthy();
      database.write((tx) => {
        tx.run(`DELETE FROM items WHERE receipt_json IS NOT NULL AND ${NO_DELIVERY_DIAGNOSTICS}`);
        // Items no longer carry files. A file that waited on an item never reached the home,
        // so the file sync must send it. `ticket_files` records only what the home holds.
        tx.run(
          'DELETE FROM ticket_files WHERE EXISTS(SELECT 1 FROM item_files f JOIN items i USING(sequence) WHERE i.receipt_json IS NULL AND f.relative_path=ticket_files.relative_path AND f.sha256=ticket_files.sha256)',
        );
        tx.run('DELETE FROM item_files');
        files.assert_healthy();
      });
      const opened = clock.monotonic();
      const sequences = database.read((sql) =>
        sql.all<{ sequence: number }>(
          'SELECT sequence FROM items WHERE receipt_json IS NULL ORDER BY sequence',
        ),
      );
      for (const row of sequences) store.first_held.set(counter(row.sequence), opened);
      await store.clear_legacy_blobs();
      return store;
    } catch (error) {
      const failure = error instanceof Error ? error : new Error(String(error));
      const database_cleanup = (failure as Partial<DurableSidecarOpenFailure>).database_cleanup;
      if (database === undefined && database_cleanup !== undefined) {
        resources.database = database_cleanup;
        resources.record_cleanup_failure((failure as DurableSidecarOpenFailure).cleanup_error);
      }
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
  private admit(): void {
    if (this.closed) throw new ServerLifecycleError('outbox is closed');
    this.assert_healthy();
  }
  private failed(error: Error): void {
    this.closed = true;
    Atomics.store(new Int32Array(this.health_fence), 0, 1);
    if (!this.faulted) {
      this.faulted = true;
      try {
        this.on_failure(error);
      } catch (error) {
        console.warn('Mac spool failure callback failed:', failure_detail(error));
      }
    }
  }
  assert_admission(): void {
    this.admit();
  }
  assert_healthy(): void {
    if (Atomics.load(new Int32Array(this.health_fence), 0))
      throw new ServerLifecycleError('Spool resource failure seals mutations.');
    try {
      this.files.assert_healthy();
      this.resources.assert_healthy();
    } catch (error) {
      if (!this.faulted) this.failed(error instanceof Error ? error : new Error(String(error)));
      throw error;
    }
  }
  with_sidecar_open<T>(work: (scope: SidecarOpenScope) => Promise<T>): Promise<T> {
    this.admit();
    let active = true;
    const require_scope = () => {
      if (!active) throw new ServerLifecycleError('sidecar opening scope is closed');
    };
    const scope: SidecarOpenScope = {
      root: this.root,
      health_fence: this.health_fence,
      assert_admission: () => {
        require_scope();
        this.admit();
      },
      assert_healthy: () => {
        require_scope();
        this.assert_healthy();
      },
      register: (service) => {
        require_scope();
        this.resources.register(service);
        service.on_failure((error) => this.failed(error));
      },
      flush_spool: () => {
        require_scope();
        this.files.assert_healthy();
        return this.files.fsync_directory(this.root);
      },
    };
    const operation = (async () => {
      await Promise.resolve();
      try {
        this.admit();
        const value = await work(scope);
        this.admit();
        return value;
      } catch (error) {
        const failure = error instanceof Error ? error : new Error(String(error));
        if (!('cleanup' in failure)) Object.assign(failure, { cleanup: this });
        throw failure;
      } finally {
        active = false;
      }
    })();
    return this.track(operation);
  }
  private track<T>(task: Promise<T>): Promise<T> {
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      (error) => {
        this.operations.delete(task);
        console.warn('Mac outbox operation failed:', failure_detail(error));
      },
    );
    return task;
  }
  lookup(submission_id: string): ItemRow | null {
    this.admit();
    return this.held(submission_id);
  }
  private held(submission_id: string): ItemRow | null {
    const row = this.database.read((sql) =>
      sql.get<ItemRow>('SELECT * FROM items WHERE submission_id=?', [submission_id]),
    );
    return row === undefined ? null : item_row(row);
  }
  delivery_started(submission_id: string): number {
    this.admit();
    const row = this.held(submission_id);
    if (row === null) throw file_error('outbox item is absent');
    const value = stored_payload(row),
      state = delivery_state(row);
    if (state.started_at_ms !== null) return state.started_at_ms;
    const started_at_ms = counter(this.clock.now_ns() / 1_000_000n);
    value.delivery = { ...state, started_at_ms };
    this.database.write((tx) => {
      tx.run('UPDATE items SET payload_json=? WHERE submission_id=?', [
        Buffer.from(JSON.stringify(value)),
        submission_id,
      ]);
      this.assert_healthy();
    });
    return started_at_ms;
  }
  fail_delivery(
    submission_id: string,
    receipt: Buffer,
    reason: string,
    confirmed: () => void,
  ): Promise<void> {
    this.admit();
    const task = (async () => {
      await Promise.resolve();
      const row = this.held(submission_id);
      if (row === null || row.receipt_json !== null) return;
      const value = stored_payload(row);
      value.delivery = { ...delivery_state(row), failure: reason };
      this.database.write((tx) => {
        tx.run('UPDATE items SET payload_json=?,receipt_json=? WHERE submission_id=?', [
          Buffer.from(JSON.stringify(value)),
          receipt,
          submission_id,
        ]);
        this.assert_healthy();
      });
      this.first_held.delete(row.sequence);
      synchronous(confirmed());
    })();
    return this.track(task);
  }
  delivery_status(): OutboxDeliveryStatus {
    this.admit();
    return this.database.read((sql) => {
      const counts = sql.get<{
        failed_count: number | bigint;
        refused_file_count: number | bigint;
      }>(
        `SELECT
          COALESCE(SUM(CASE WHEN json_extract(CAST(payload_json AS TEXT),'$.delivery.failure') IS NOT NULL THEN 1 ELSE 0 END),0) AS failed_count,
          COALESCE(SUM(COALESCE(json_array_length(CAST(payload_json AS TEXT),'$.delivery.file_refusals'),0)),0) AS refused_file_count
        FROM items WHERE NOT(${NO_DELIVERY_DIAGNOSTICS})`,
      )!;
      const latest = sql.get<{ reason: string | null }>(
        `SELECT COALESCE(
          json_extract(CAST(payload_json AS TEXT),'$.delivery.failure'),
          json_extract(CAST(payload_json AS TEXT),'$.delivery.file_refusals[#-1].reason')
        ) AS reason FROM items WHERE NOT(${NO_DELIVERY_DIAGNOSTICS}) ORDER BY sequence DESC LIMIT 1`,
      );
      return {
        failed_count: counter(counts.failed_count),
        refused_file_count: counter(counts.refused_file_count),
        last_error: latest?.reason ?? null,
      };
    });
  }
  pending(conversation_id?: string): ItemRow[] {
    this.admit();
    return this.database
      .read((sql) =>
        conversation_id === undefined
          ? sql.all<ItemRow>(
              'SELECT * FROM items WHERE receipt_json IS NULL ORDER BY sequence LIMIT ?',
              [MAX_ITEM_BATCH_ITEMS],
            )
          : sql.all<ItemRow>(
              'SELECT * FROM items WHERE receipt_json IS NULL AND conversation_id=? ORDER BY sequence LIMIT ?',
              [safe_component(conversation_id), MAX_ITEM_BATCH_ITEMS],
            ),
      )
      .map(item_row);
  }
  pending_conversations(
    limit: number | null = MAX_LEASE_WAITING_CONVERSATIONS + 1,
  ): readonly (readonly [string, number])[] {
    this.admit();
    return this.database.read((sql) =>
      sql
        .all<{ conversation_id: string; oldest: number }>(
          'SELECT conversation_id,MIN(sequence) AS oldest FROM items WHERE receipt_json IS NULL GROUP BY conversation_id ORDER BY MIN(sequence)' +
            (limit === null ? '' : ' LIMIT ?'),
          limit === null ? [] : [limit],
        )
        .map((row) => [row.conversation_id, counter(row.oldest)] as const),
    );
  }
  pending_count(): number {
    this.admit();
    return counter(
      this.database.read(
        (sql) =>
          sql.get<{ count: number }>(
            'SELECT COUNT(*) AS count FROM items WHERE receipt_json IS NULL',
          )!.count,
      ),
    );
  }
  oldest_age_ms(): number | null {
    this.admit();
    const first = this.database.read((sql) =>
      sql.get<{ sequence: number }>(
        'SELECT sequence FROM items WHERE receipt_json IS NULL ORDER BY sequence LIMIT 1',
      ),
    );
    const held = first === undefined ? undefined : this.first_held.get(counter(first.sequence));
    return held === undefined ? null : Math.trunc((this.clock.monotonic() - held) * 1000);
  }
  append(columns: ItemColumns, payload: Buffer, validate: (row: ItemRow) => void): ItemRow {
    this.admit();
    if (columns.submission_id !== undefined) {
      const prior = this.held(safe_component(columns.submission_id));
      if (prior !== null) return prior;
    }
    const submission = safe_component(columns.submission_id || randomUUID().replaceAll('-', ''));
    safe_component(columns.conversation_id);
    const size_guard = item_size_guard(this.sender_id, columns, submission, payload);
    this.assert_healthy();
    const row = this.database.write((tx) => {
      const result = tx.run(
        'INSERT INTO items(submission_id,conversation_id,child_id,kind,payload_json) VALUES(?,?,?,?,?)',
        [submission, columns.conversation_id, columns.child_id, columns.kind, payload],
      );
      const sequence = counter(result.lastInsertRowid);
      size_guard(sequence);
      const held = item_row(tx.get<ItemRow>('SELECT * FROM items WHERE sequence=?', [sequence])!);
      synchronous(validate(held));
      this.assert_healthy();
      return held;
    });
    this.first_held.set(row.sequence, this.clock.monotonic());
    this.pending_events.emit('pending');
    return row;
  }
  /** The file sync listens for this. A Ticket's files can differ after any of its items. */
  note_ticket_activity(ticket_id: string): void {
    this.pending_events.emit('ticket_activity', safe_component(ticket_id));
  }
  /** The file versions that the home confirmed for this Ticket. */
  home_files(ticket_id: string): TicketFileRow[] {
    this.admit();
    return this.database
      .read((sql) =>
        sql.all<TicketFileRow>('SELECT * FROM ticket_files WHERE ticket_id=?', [ticket_id]),
      )
      .map((row) => ({
        ...row,
        byte_count: counter(row.byte_count),
        modified_ns: BigInt(row.modified_ns),
      }));
  }
  record_home_file(file: TicketFileRow): void {
    this.admit();
    this.database.write((tx) => {
      tx.run(
        'INSERT INTO ticket_files VALUES(?,?,?,?,?) ON CONFLICT(ticket_id,relative_path) DO UPDATE SET byte_count=excluded.byte_count,modified_ns=excluded.modified_ns,sha256=excluded.sha256',
        [file.ticket_id, file.relative_path, file.byte_count, file.modified_ns, file.sha256],
      );
      this.assert_healthy();
    });
  }
  forget_home_file(ticket_id: string, relative_path: string): void {
    this.admit();
    this.database.write((tx) => {
      tx.run('DELETE FROM ticket_files WHERE ticket_id=? AND relative_path=?', [
        ticket_id,
        relative_path,
      ]);
      this.assert_healthy();
    });
  }
  acknowledge(
    submission_id: string,
    sender_id: string,
    receipt: Buffer,
    confirmed: () => void,
  ): Promise<void> {
    this.admit();
    const task = (async () => {
      // Register the whole operation before a host callback can request shutdown.
      await Promise.resolve();
      const row = this.held(submission_id);
      if (row === null) return;
      if (sender_id !== this.sender_id) throw file_error('receipt identity mismatch');
      this.assert_healthy();
      this.database.write((tx) => {
        if (row.kind === 'cli_write' || delivery_diagnostics(row))
          tx.run('UPDATE items SET receipt_json=? WHERE submission_id=?', [receipt, submission_id]);
        else tx.run('DELETE FROM items WHERE submission_id=?', [submission_id]);
        if (row.kind === 'child_stopped')
          tx.run(
            `DELETE FROM items WHERE child_id=? AND receipt_json IS NOT NULL AND ${NO_DELIVERY_DIAGNOSTICS}`,
            [row.child_id],
          );
        this.assert_healthy();
      });
      this.first_held.delete(row.sequence);
      synchronous(confirmed());
    })();
    return this.track(task);
  }
  /** Older runtimes copied each file into `blobs` before they sent it. Nothing uses them now. */
  private async clear_legacy_blobs(): Promise<void> {
    const path = join(this.root, 'blobs');
    if (!(await exists(path))) return;
    try {
      const blobs = await this.files.open_directory(path);
      try {
        await this.files.sweep_directory(blobs, path, []);
      } finally {
        await this.files.close_directory(blobs);
      }
    } catch (error) {
      console.warn('Mac outbox could not clear old file copies:', failure_detail(error));
    }
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.drained ??= (async () => {
      // An admitted synchronous append must leave its writer scope before close.
      await Promise.resolve();
      while (this.operations.size) await Promise.allSettled([...this.operations]);
    })();
    // Delegate every stop attempt after the admitted work drains. A failed stop-frame
    // send remains retryable.
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
