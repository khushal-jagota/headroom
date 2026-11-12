/** The spool owner registers this unstarted service before any journal worker opens SQLite. */
import { Worker } from 'node:worker_threads';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { FILE_CHUNK_BYTES, MAX_MAILBOX_POLL_BYTES } from '../configuration.ts';
import { JOURNAL_OPERATIONS, JOURNAL_INPUT_BYTES } from './journal_limits.ts';
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
  FreshCommandCursor,
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
  private received = 0;
  private remote_close: Promise<void> | undefined;
  private readonly released: () => void;
  constructor(
    service: CommandJournal,
    descriptor: ReaderDescriptor,
    private_delivery = false,
    released = () => {},
  ) {
    this.service = service;
    this.id = descriptor.reader;
    this.byte_count = descriptor.byte_count;
    this.critical = private_delivery;
    this.released = released;
    if (!Number.isSafeInteger(this.byte_count) || this.byte_count < 0)
      throw service.delivery_failure('Journal reader has an invalid byte count.');
  }
  private readonly critical: boolean;
  get incomplete(): boolean {
    return this.received !== this.byte_count;
  }
  release(): void {
    this.ended = true;
    this.released();
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
          if (this.incomplete)
            throw this.service.delivery_failure('Journal reader ended before its byte count.');
          this.ended = true;
          this.released();
          break;
        }
        if (
          !(value instanceof ArrayBuffer) ||
          value.byteLength === 0 ||
          value.byteLength > FILE_CHUNK_BYTES ||
          this.received + value.byteLength > this.byte_count
        )
          throw this.service.delivery_failure('Journal reader exceeded its chunk or byte count.');
        this.received += value.byteLength;
        if (!this.incomplete) {
          this.ended = true;
          this.released();
        }
        yield new Uint8Array(value);
      }
      complete = true;
    } catch (error) {
      if (this.critical)
        throw this.service.delivery_failure('Fresh journal command delivery failed.', error);
      throw error;
    } finally {
      if (!complete) await this.close();
    }
  }
  async close(): Promise<void> {
    if (!this.ended) {
      this.ended = true;
      this.released();
      if (this.critical && this.incomplete)
        this.service.delivery_failure('Fresh journal command delivery was abandoned.');
    }
    const task = this.remote_close ?? this.service.close_reference('reader_close', this.id);
    this.remote_close = task;
    try {
      await task;
    } catch (error) {
      if (this.remote_close === task) this.remote_close = undefined;
      throw error;
    }
  }
}
class Cursor<T> implements JournalCursor<T> {
  private readonly service: CommandJournal;
  private readonly id: number;
  private readonly map: (
    row: CursorRowDescriptor,
    reader: (value: ReaderDescriptor) => Reader,
  ) => T;
  readonly command_count: number;
  private readonly readers = new Set<Reader>();
  private issued = 0;
  private readonly fresh_count: number | undefined;
  private untrack = () => {};
  private get incomplete(): boolean {
    return (
      this.fresh_count !== undefined &&
      (this.issued < this.fresh_count || [...this.readers].some((reader) => reader.incomplete))
    );
  }
  private exhausted = false;
  private closed = false;
  private remote_close: Promise<void> | undefined;
  private busy = false;
  constructor(
    service: CommandJournal,
    id: number,
    map: (row: CursorRowDescriptor, reader: (value: ReaderDescriptor) => Reader) => T,
    fresh?: { command_count: number; fresh_count: number },
  ) {
    this.service = service;
    this.id = id;
    this.map = map;
    this.command_count = fresh?.command_count ?? 0;
    this.fresh_count = fresh?.fresh_count;
    if (this.fresh_count) this.untrack = service.track_delivery(() => this.incomplete);
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
          if (this.fresh_count !== undefined && this.issued !== this.fresh_count)
            throw this.service.delivery_failure(
              'Fresh journal batch ended before its command count.',
            );
          this.exhausted = true;
          break;
        }
        this.issued++;
        if (this.fresh_count !== undefined && this.issued > this.fresh_count)
          throw this.service.delivery_failure('Fresh journal batch exceeded its command count.');
        yield this.map(value, (descriptor) => {
          const reader = new Reader(
            this.service,
            descriptor,
            this.fresh_count !== undefined,
            () => {
              this.readers.delete(reader);
              if (!this.incomplete) this.untrack();
            },
          );
          this.readers.add(reader);
          return reader;
        });
      }
      complete = true;
    } catch (error) {
      if (this.fresh_count !== undefined)
        throw this.service.delivery_failure('Fresh journal batch delivery failed.', error);
      throw error;
    } finally {
      if (!complete) await this.close();
    }
  }
  async close(): Promise<void> {
    if (!this.closed) {
      this.closed = true;
      if (this.incomplete)
        this.service.delivery_failure('Fresh journal batch delivery was abandoned.');
      this.untrack();
      for (const reader of this.readers) reader.release();
      this.readers.clear();
    }
    const task = this.remote_close ?? this.service.close_reference('cursor_close', this.id);
    this.remote_close = task;
    try {
      await task;
    } catch (error) {
      if (this.remote_close === task) this.remote_close = undefined;
      throw error;
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
  private stop_acknowledged = false;
  private failure: Error | undefined;
  private readonly listeners = new Set<(error: Error) => void>();
  private readonly operations = new Set<Promise<unknown>>();
  private tail: Promise<void> = Promise.resolve();
  private readonly deliveries = new Set<() => boolean>();
  private readonly callbacks = new Set<Promise<unknown>>();
  private readonly pending = new Map<
    number,
    { resolve(value: unknown): void; reject(error: Error): void; reply_error?(error: Error): void }
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
    if (!this.stop_acknowledged && this.worker?.threadId === -1)
      this.fail(new ServerLifecycleError('Journal worker is unavailable.'), true);
    if (this.failure) throw this.failure;
  }
  on_failure(listener: (error: Error) => void): () => void {
    this.listeners.add(listener);
    if (this.failure) listener(this.failure);
    return () => this.listeners.delete(listener);
  }
  private fail(error: Error, worker_lost = false): void {
    const first = this.failure === undefined;
    this.failure ??= error;
    this.closed = true;
    this.ready_reject?.(error);
    for (const [id, value] of this.pending) {
      // A delivery refusal does not cancel an in-flight native close response.
      if (worker_lost || !value.reply_error) {
        value.reject(error);
        this.pending.delete(id);
      }
    }
    if (first) {
      for (const listener of this.listeners) listener(error);
      try {
        this.dependencies.on_failure(error);
      } catch (error) {
        console.warn('Mac journal failure callback failed:', failure_name(error));
      }
    }
  }
  delivery_failure(message: string, cause?: unknown): Error {
    const error = new ServerLifecycleError(message);
    if (cause !== undefined) Object.assign(error, { cause });
    this.fail(error);
    return this.failure!;
  }
  track_delivery(incomplete: () => boolean): () => void {
    this.deliveries.add(incomplete);
    return () => this.deliveries.delete(incomplete);
  }
  private transport_failure(worker: Worker, error: unknown): void {
    this.fail(error instanceof Error ? error : new Error(String(error)), true);
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
        worker.on('error', (error) => this.fail(error, true));
        worker.on('exit', (code) => {
          if (!this.stop_acknowledged || this.pending.size)
            this.fail(new ServerLifecycleError(`Journal worker exited (${code}).`), true);
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
      if (message.error) {
        const error = this.restore(message.error);
        if (request?.reply_error) request.reply_error(error);
        else request?.reject(error);
      } else request?.resolve(message.value);
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
      try {
        this.assert_healthy();
      } catch (error) {
        reject(error);
        return;
      }
      const worker = this.worker;
      if (!worker || worker.threadId === -1) {
        reject(this.failure ?? new ServerLifecycleError('Journal worker is unavailable.'));
        return;
      }
      if (this.pending.size >= JOURNAL_OPERATIONS) {
        reject(new ServerLifecycleError('Journal request capacity reached.'));
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
    return this.enqueue(() => this.raw(action) as Promise<T>);
  }
  private enqueue<T>(work: () => Promise<T>): Promise<T> {
    try {
      this.admit();
      if (this.operations.size >= JOURNAL_OPERATIONS)
        throw new ServerLifecycleError('Journal operation capacity reached.');
      const task = this.tail.then(() => {
        this.assert_healthy();
        return work();
      });
      this.tail = task.then(
        () => {},
        () => {},
      );
      return this.hold(task);
    } catch (error) {
      return Promise.reject(error);
    }
  }
  private with_input<T>(
    value: Uint8Array | string,
    work: (input: number) => Promise<T>,
  ): Promise<T> {
    if (value.length > JOURNAL_INPUT_BYTES)
      return Promise.reject(new RangeError('Journal input exceeds the native byte limit.'));
    return this.enqueue(async () => {
      const input = (await this.raw({
        operation: 'input_start',
        text: typeof value === 'string',
      })) as number;
      try {
        const length = value.length,
          stride = typeof value === 'string' ? Math.floor(FILE_CHUNK_BYTES / 3) : FILE_CHUNK_BYTES;
        for (let offset = 0; offset < length; offset += stride) {
          const chunk =
            typeof value === 'string'
              ? value.slice(offset, offset + stride)
              : Uint8Array.from(value.subarray(offset, offset + stride)).buffer;
          await this.raw({ operation: 'input_push', input, chunk });
        }
        await this.raw({ operation: 'input_end', input });
        return await work(input);
      } finally {
        if (!this.failure) await this.raw({ operation: 'input_drop', input });
      }
    });
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
  ): Promise<FreshCommandCursor> {
    if (
      bytes.length > MAX_MAILBOX_POLL_BYTES ||
      (typeof bytes === 'string' && Buffer.byteLength(bytes) > MAX_MAILBOX_POLL_BYTES)
    )
      throw new RangeError('Mailbox poll reply exceeds its body limit.');
    return this.with_input(bytes, async (input) => {
      const value = (await this.raw({
        operation: 'record_poll_reply',
        input,
        incarnation,
        after: polled_after,
      })) as { cursor: number; command_count: number; fresh_count: number };
      return new Cursor(
        this,
        value.cursor,
        (row, reader) => ({
          command_id: row.command_id,
          command: reader(row.command!),
        }),
        value,
      );
    });
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
    return new Cursor(this, value.cursor, (row, reader) => ({
      command_id: row.command_id,
      command: reader(row.command!),
      result: reader(row.result!),
    }));
  }
  async unreported(limit: number): Promise<JournalCursor<UnreportedJournalResult>> {
    const value = await this.call<{ cursor: number }>({ operation: 'unreported', limit });
    return new Cursor(this, value.cursor, (row, reader) => ({
      command_id: row.command_id,
      result: reader(row.result!),
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
    await this.call(
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
    if (!this.stop_request) {
      const request = (async () => {
        // Admission is sealed, but complete uploads and their final transaction keep
        // their reservation. A stop frame cannot split an admitted operation.
        while (this.operations.size) await Promise.allSettled([...this.operations]);
        if ([...this.deliveries].some((incomplete) => incomplete()))
          this.delivery_failure('Shutdown abandoned a committed fresh journal batch.');
        this.deliveries.clear();
        const worker = this.worker;
        if (worker && worker.threadId !== -1) {
          const id = ++this.sequence;
          let close_error: Error | undefined;
          try {
            close_error = await new Promise<Error | undefined>((resolve, reject) => {
              this.pending.set(id, {
                resolve: () => {
                  this.stop_acknowledged = true;
                  resolve(undefined);
                },
                reject,
                reply_error: resolve,
              });
              try {
                worker.postMessage({ kind: 'stop', id } satisfies JournalMainMessage);
              } catch (error) {
                this.pending.delete(id);
                this.transport_failure(worker, error);
                reject(error);
              }
            });
          } catch (error) {
            // Worker loss rejects pending requests and must join actual exit.
            if (!this.failure) throw error;
          }
          // A real close-error reply proves a live retry port, even after an
          // earlier delivery fault. Keep it distinct from fail() rejection.
          if (close_error) throw close_error;
        } else if (worker)
          this.fail(new ServerLifecycleError('Journal worker is unavailable.'), true);
        await this.join_actual();
        if (this.failure) {
          const error = new ServerLifecycleError(
            'Journal stopped after an owned resource failure.',
          );
          throw Object.assign(error, { cause: this.failure });
        }
      })();
      this.stop_request = request;
      void request.catch(() => {
        if (this.stop_request === request) this.stop_request = undefined;
      });
    }
    return observe_shutdown(
      this.stop_request.then(() => undefined),
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
