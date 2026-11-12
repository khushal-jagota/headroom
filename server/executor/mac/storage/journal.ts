/** The spool owner registers this unstarted service before any journal worker opens SQLite. */
import { Worker } from 'node:worker_threads';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { FILE_CHUNK_BYTES } from '../configuration.ts';
import { failure_name } from '../failure_name.ts';
import { journal_error, journal_error_properties } from './journal_worker_contracts.ts';
import type {
  JournalAction,
  JournalErrorBody,
  JournalMainMessage,
  JournalWorkerMessage,
  ReaderDescriptor,
  CursorRowDescriptor,
} from './journal_worker_contracts.ts';
import type {
  AsyncCommandJournal,
  CanonicalReader,
  JournalCursor,
  JournalDependencies,
  JournalSpoolOwner,
  SidecarOpenScope,
  FreshJournalCommand,
  UnpublishedJournalCommand,
  UnreportedJournalResult,
} from './journal_contracts.ts';
export type * from './journal_contracts.ts';

class Reader implements CanonicalReader {
  readonly byte_count: number;
  private readonly service: CommandJournal;
  private readonly id: number;
  private ended = false;
  private busy = false;
  constructor(service: CommandJournal, descriptor: ReaderDescriptor) {
    this.service = service;
    this.id = descriptor.reader;
    this.byte_count = descriptor.byte_count;
  }
  async *[Symbol.asyncIterator](): AsyncGenerator<Uint8Array> {
    let complete = false;
    try {
      while (!this.ended) {
        if (this.busy) throw new Error('journal reader already has an outstanding pull');
        this.busy = true;
        let value: ArrayBuffer | null;
        try {
          value = await this.service.reader_next(this.id);
        } finally {
          this.busy = false;
        }
        if (value === null) {
          this.ended = true;
          break;
        }
        yield new Uint8Array(value);
      }
      complete = true;
    } finally {
      if (!complete) await this.close();
    }
  }
  async close(): Promise<void> {
    if (!this.ended) {
      this.ended = true;
      await this.service.close_reference('reader_close', this.id);
    }
  }
}
class Cursor<T> implements JournalCursor<T> {
  private readonly service: CommandJournal;
  private readonly id: number;
  private readonly map: (row: CursorRowDescriptor) => T;
  private exhausted = false;
  private closed = false;
  private busy = false;
  constructor(service: CommandJournal, id: number, map: (row: CursorRowDescriptor) => T) {
    this.service = service;
    this.id = id;
    this.map = map;
  }
  async *[Symbol.asyncIterator](): AsyncGenerator<T> {
    let complete = false;
    try {
      while (!this.closed && !this.exhausted) {
        if (this.busy) throw new Error('journal cursor already has an outstanding pull');
        this.busy = true;
        let value: CursorRowDescriptor | null;
        try {
          value = await this.service.cursor_next(this.id);
        } finally {
          this.busy = false;
        }
        if (value === null) {
          this.exhausted = true;
          break;
        }
        yield this.map(value);
      }
      complete = true;
    } finally {
      if (!complete) await this.close();
    }
  }
  async close(): Promise<void> {
    if (!this.closed) {
      this.closed = true;
      await this.service.close_reference('cursor_close', this.id);
    }
  }
}
export class CommandJournal implements AsyncCommandJournal {
  private readonly owner: JournalSpoolOwner;
  private readonly incarnation: string;
  private readonly dependencies: JournalDependencies;
  private scope: SidecarOpenScope | undefined;
  private worker: Worker | undefined;
  private exit: Promise<void> = Promise.resolve();
  private closed = false;
  private failure: Error | undefined;
  private readonly listeners = new Set<(error: Error) => void>();
  private readonly operations = new Set<Promise<unknown>>();
  private readonly callbacks = new Set<Promise<unknown>>();
  private readonly pending = new Map<
    number,
    { resolve(value: unknown): void; reject(error: Error): void }
  >();
  private sequence = 0;
  private ready_resolve!: () => void;
  private ready_reject!: (error: Error) => void;
  private ready: Promise<void> = Promise.resolve();
  private stop_request: Promise<unknown> | undefined;
  private joined: Promise<void> | undefined;
  private readonly actual: Promise<void>;
  private actual_resolve!: () => void;
  private error_exports: Record<string, unknown> = {};
  private constructor(
    owner: JournalSpoolOwner,
    incarnation: string,
    dependencies: JournalDependencies,
  ) {
    this.owner = owner;
    this.incarnation = incarnation;
    this.dependencies = dependencies;
    this.actual = new Promise((resolve) => {
      this.actual_resolve = resolve;
    });
    for (const url of [dependencies.protocol_module, dependencies.outputs_module])
      if (url.protocol !== 'file:')
        throw new TypeError('journal dependencies must be trusted file module URLs');
  }
  assert_healthy(): void {
    if (!this.closed && this.worker?.threadId === -1)
      this.fail(new ServerLifecycleError('Journal worker is unavailable.'));
    if (this.failure) throw this.failure;
  }
  on_failure(listener: (error: Error) => void): () => void {
    this.listeners.add(listener);
    if (this.failure) listener(this.failure);
    return () => this.listeners.delete(listener);
  }
  private fail(error: Error): void {
    const first = this.failure === undefined;
    this.failure ??= error;
    this.closed = true;
    this.ready_reject?.(error);
    for (const value of this.pending.values()) value.reject(error);
    this.pending.clear();
    if (first) for (const listener of this.listeners) listener(error);
  }
  private transport_failure(worker: Worker, error: unknown): void {
    this.fail(error instanceof Error ? error : new Error(String(error)));
    // A broken callback transport cannot drain its waiter. This is a fatal transport
    // failure, not normal close: native addon cleanup runs before actual worker exit.
    void worker.terminate().catch(() => {});
  }
  private restore(body: JournalErrorBody): Error {
    const error = Object.assign(new Error(body.message), journal_error_properties(body), {
      name: body.name,
    });
    const shared = this.error_exports[body.name];
    if (typeof shared === 'function' && shared.prototype instanceof Error)
      Object.setPrototypeOf(error, shared.prototype);
    else if (body.name === 'TypeError') Object.setPrototypeOf(error, TypeError.prototype);
    else if (body.name === 'ServerLifecycleError')
      Object.setPrototypeOf(error, ServerLifecycleError.prototype);
    return error;
  }
  private hold<T>(task: Promise<T>, log = true): Promise<T> {
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      (error) => {
        this.operations.delete(task);
        if (log) console.warn('Mac journal operation failed:', failure_name(error));
      },
    );
    return task;
  }
  private initialize(scope: SidecarOpenScope): Promise<void> {
    this.scope = scope;
    return this.hold(
      (async () => {
        // Import only establishes shared error prototypes on main; it runs no codec calls.
        this.error_exports = await import(this.dependencies.protocol_module.href);
        scope.assert_admission();
        if (this.closed) throw new ServerLifecycleError('Journal opening is stopped.');
        this.ready = new Promise<void>((resolve, reject) => {
          this.ready_resolve = resolve;
          this.ready_reject = reject;
        });
        void this.ready.catch(() => {});
        const worker = new Worker(new URL('./journal_worker.ts', import.meta.url), {
          execArgv: [],
          workerData: {
            root: scope.root,
            incarnation: this.incarnation,
            protocol_module: this.dependencies.protocol_module.href,
            outputs_module: this.dependencies.outputs_module.href,
            health_fence: this.owner.health_fence,
          },
        });
        this.worker = worker;
        this.exit = new Promise((resolve) => worker.once('exit', () => resolve()));
        worker.on('message', (message: JournalWorkerMessage) => this.receive(worker, message));
        worker.on('error', (error) => this.fail(error));
        worker.on('exit', (code) => {
          if (!this.closed || this.pending.size)
            this.fail(new Error(`Journal worker exited (${code}).`));
          this.worker = undefined;
          this.join_actual();
        });
        await this.ready;
        scope.assert_admission();
        if (this.closed) throw new ServerLifecycleError('Journal opening is stopped.');
        await this.raw({ operation: 'open' });
        scope.assert_admission();
      })(),
      false,
    );
  }
  private receive(worker: Worker, message: JournalWorkerMessage): void {
    if (message.kind === 'ready') {
      this.ready_resolve();
      return;
    }
    if (message.kind === 'startup_error') {
      this.ready_reject(this.restore(message.error));
      return;
    }
    if (message.kind === 'reply') {
      const request = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) request?.reject(this.restore(message.error));
      else request?.resolve(message.value);
      return;
    }
    const task = Promise.resolve().then<bigint | void>(() => {
      if (message.callback === 'permission') {
        if (this.closed) throw new ServerLifecycleError('Journal opening is stopped.');
        this.scope!.assert_admission();
        this.scope!.assert_healthy();
        return;
      }
      if (message.callback === 'clock') return this.dependencies.now_ms();
      return this.scope!.flush_spool();
    });
    this.callbacks.add(task);
    const reply = (value?: bigint, error?: unknown) => {
      this.callbacks.delete(task);
      if (this.worker !== worker || worker.threadId === -1) return;
      try {
        worker.postMessage({
          kind: 'callback_result',
          id: message.id,
          ...(error === undefined ? { value } : { error: journal_error(error) }),
        } satisfies JournalMainMessage);
      } catch (failure) {
        this.transport_failure(worker, failure);
      }
    };
    void task.then(
      (value) => reply(typeof value === 'bigint' ? value : undefined),
      (error) => reply(undefined, error),
    );
  }
  private raw(action: JournalAction): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const worker = this.worker;
      if (!worker || worker.threadId === -1) {
        reject(this.failure ?? new ServerLifecycleError('Journal worker is unavailable.'));
        return;
      }
      const id = ++this.sequence;
      this.pending.set(id, { resolve, reject });
      try {
        worker.postMessage(
          { kind: 'request', id, action } satisfies JournalMainMessage,
          action.operation === 'input_push' && action.chunk instanceof ArrayBuffer
            ? [action.chunk]
            : [],
        );
      } catch (error) {
        const failure = error instanceof Error ? error : new Error(String(error));
        this.transport_failure(worker, failure);
        reject(failure);
      }
    });
  }
  private admit(): void {
    if (this.closed) throw this.failure ?? new ServerLifecycleError('Journal is stopped.');
    this.assert_healthy();
    this.owner.assert_admission();
  }
  private call<T>(action: JournalAction): Promise<T> {
    try {
      this.admit();
      return this.hold(this.raw(action) as Promise<T>);
    } catch (error) {
      return Promise.reject(error);
    }
  }
  private with_input<T>(
    value: Uint8Array | string,
    work: (input: number) => Promise<T>,
  ): Promise<T> {
    try {
      this.admit();
    } catch (error) {
      return Promise.reject(error);
    }
    return this.hold(
      (async () => {
        const input = (await this.raw({
          operation: 'input_start',
          text: typeof value === 'string',
        })) as number;
        try {
          const length = value.length,
            stride =
              typeof value === 'string' ? Math.floor(FILE_CHUNK_BYTES / 3) : FILE_CHUNK_BYTES;
          for (let offset = 0; offset < length; offset += stride) {
            this.admit();
            const chunk =
              typeof value === 'string'
                ? value.slice(offset, offset + stride)
                : Uint8Array.from(value.subarray(offset, offset + stride)).buffer;
            await this.raw({ operation: 'input_push', input, chunk });
          }
          this.admit();
          await this.raw({ operation: 'input_end', input });
          this.admit();
          return await work(input);
        } finally {
          if (!this.closed) await this.raw({ operation: 'input_drop', input });
        }
      })(),
    );
  }
  poll_cursor(): Promise<bigint> {
    return this.call({ operation: 'poll_cursor' });
  }
  lower_poll_cursor(high: bigint, cursor_at_lease: bigint): Promise<boolean> {
    return this.call({ operation: 'lower_poll_cursor', high, cursor_at_lease });
  }
  async record_poll_reply(
    bytes: Uint8Array | string,
    incarnation: string,
    polled_after: bigint,
  ): Promise<JournalCursor<FreshJournalCommand>> {
    const value = (await this.with_input(bytes, (input) =>
      this.raw({ operation: 'record_poll_reply', input, incarnation, after: polled_after }),
    )) as { cursor: number };
    return new Cursor(this, value.cursor, (row) => ({
      command_id: row.command_id,
      command: new Reader(this, row.command!),
    }));
  }
  async result_of(command_id: string): Promise<CanonicalReader | null> {
    const value = await this.call<ReaderDescriptor | null>({ operation: 'result_of', command_id });
    return value === null ? null : new Reader(this, value);
  }
  finish(command_id: string, result: Uint8Array | string, outputs_owed: boolean): Promise<void> {
    return this.with_input(
      result,
      (input) =>
        this.raw({ operation: 'finish', command_id, input, owed: outputs_owed }) as Promise<void>,
    );
  }
  mark_outputs_published(command_id: string): Promise<void> {
    return this.call({ operation: 'mark_outputs_published', command_id });
  }
  async unpublished(): Promise<JournalCursor<UnpublishedJournalCommand>> {
    const value = await this.call<{ cursor: number }>({ operation: 'unpublished' });
    return new Cursor(this, value.cursor, (row) => ({
      command_id: row.command_id,
      command: new Reader(this, row.command!),
      result: new Reader(this, row.result!),
    }));
  }
  async unreported(limit: number): Promise<JournalCursor<UnreportedJournalResult>> {
    const value = await this.call<{ cursor: number }>({ operation: 'unreported', limit });
    return new Cursor(this, value.cursor, (row) => ({
      command_id: row.command_id,
      result: new Reader(this, row.result!),
    }));
  }
  mark_reported(command_ids: readonly string[]): Promise<void> {
    return this.call({ operation: 'mark_reported', command_ids });
  }
  shrink_to_tombstones(): Promise<void> {
    return this.call({ operation: 'shrink_to_tombstones' });
  }
  reader_next(reader: number): Promise<ArrayBuffer | null> {
    return this.call({ operation: 'reader_next', reader });
  }
  cursor_next(cursor: number): Promise<CursorRowDescriptor | null> {
    return this.call({ operation: 'cursor_next', cursor });
  }
  async close_reference(operation: 'reader_close' | 'cursor_close', id: number): Promise<void> {
    if (this.closed) return;
    await this.raw(
      operation === 'reader_close' ? { operation, reader: id } : { operation, cursor: id },
    );
  }
  private join_actual(): Promise<void> {
    this.joined ??= (async () => {
      await this.exit;
      while (this.operations.size || this.callbacks.size)
        await Promise.allSettled([...this.operations, ...this.callbacks]);
      this.actual_resolve();
    })();
    return this.joined;
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    if (this.worker && !this.stop_request) {
      const id = ++this.sequence;
      const request = new Promise<unknown>((resolve, reject) => {
        this.pending.set(id, { resolve, reject });
        try {
          this.worker!.postMessage({ kind: 'stop', id } satisfies JournalMainMessage);
        } catch (error) {
          this.pending.delete(id);
          reject(error);
        }
      });
      this.stop_request = request;
      void request.catch(() => {
        if (this.stop_request === request) this.stop_request = undefined;
      });
    }
    return observe_shutdown(
      Promise.all([this.stop_request, this.join_actual()]).then(() => undefined),
      options,
      'Journal resources remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.actual;
  }
  static open(
    owner: JournalSpoolOwner,
    incarnation: string,
    dependencies: JournalDependencies,
    options: StopOptions,
  ): Promise<AsyncCommandJournal> {
    return owner.with_sidecar_open(async (scope) => {
      const journal = new CommandJournal(owner, incarnation, dependencies);
      scope.register(journal);
      try {
        await journal.initialize(scope);
        scope.assert_admission();
        return journal;
      } catch (error) {
        const original = error instanceof Error ? error : new Error(String(error));
        try {
          await journal.stop(options);
        } catch (cleanup_error) {
          const outward =
            cleanup_error instanceof Error ? cleanup_error : new Error(String(cleanup_error));
          Object.assign(outward, { cause: original, cleanup: journal });
          throw outward;
        }
        throw original;
      }
    });
  }
}
export function open_command_journal(
  owner: JournalSpoolOwner,
  incarnation: string,
  dependencies: JournalDependencies,
  options: StopOptions,
): Promise<AsyncCommandJournal> {
  return CommandJournal.open(owner, incarnation, dependencies, options);
}
