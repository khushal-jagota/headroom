/** Durable rows own commands. This owner supplies only waits and commit announcements. */
import type { Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { observe_shutdown, is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type {
  CommandSettlementReason,
  MailboxCounter,
  MailboxRow,
  MailboxSettlements,
  MailboxSettledListener,
  PromptWrittenAgain,
  LateSteerAfterRestart,
  WireValue,
} from './contracts.ts';
import { now_ms } from './clock.ts';
import * as data from './data/mailbox.ts';
import { integer_value } from './protocol.ts';
import { normalize_integer } from './integers.ts';

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T | PromiseLike<T>): void;
  reject(reason: unknown): void;
}
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, failed) => {
    resolve = done;
    reject = failed;
  });
  return { promise, resolve, reject };
}
export class SqliteCommandMailbox {
  settled_listener: MailboxSettledListener | null = null;
  private readonly waiters = new Map<string, Set<Deferred<WireValue<'CommandResult'>>>>();
  private readonly operations = new Set<Promise<unknown>>();
  private readonly cleanup_failures = new Set<unknown>();
  private readonly resource_join = deferred<void>();
  private readonly closed_waiter_error = new DOMException(
    'Executor mailbox is closed.',
    'AbortError',
  );
  private put_event = deferred<void>();
  private high: bigint | null = null;
  private closed = false;
  private readonly clock: () => number;
  private readonly database: Database;

  constructor(database: Database, options: { clock?: () => number } = {}) {
    this.database = database;
    this.clock = options.clock ?? now_ms;
    // Retain the raw rejection even when a bounded stop observer already expired.
    void this.resource_join.promise.catch(() => {});
  }
  private admit(signal?: AbortSignal): void {
    if (this.closed) throw new ServerLifecycleError('Executor mailbox is closed.');
    signal?.throwIfAborted();
  }
  private retain<T>(operation: Promise<T>): Promise<T> {
    this.operations.add(operation);
    void operation.then(
      () => this.operations.delete(operation),
      () => this.operations.delete(operation),
    );
    return operation;
  }
  private observe<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T>;
  private observe<T>(
    promise: Promise<T>,
    signal: AbortSignal | undefined,
    timeout: number | null,
  ): Promise<T | null>;
  private observe<T>(
    promise: Promise<T>,
    signal?: AbortSignal,
    timeout: number | null = null,
  ): Promise<T | null> {
    const observed = new Promise<T | null>((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      let finished = false;
      const finish = (outcome: { value: T | null } | { error: unknown }) => {
        if (finished) return;
        finished = true;
        const failures: unknown[] = [];
        try {
          clearTimeout(timer);
        } catch (error) {
          failures.push(error);
        }
        try {
          signal?.removeEventListener('abort', abort);
        } catch (error) {
          failures.push(error);
        }
        if (failures.length) {
          if ('error' in outcome) failures.push(outcome.error);
          const failure = new AggregateError(failures, 'Executor mailbox wait cleanup failed.');
          this.cleanup_failures.add(failure);
          console.error('Executor mailbox wait cleanup failed.', failure);
          reject(failure);
        } else if ('error' in outcome) reject(outcome.error);
        else resolve(outcome.value);
      };
      const abort = () => finish({ error: signal!.reason });
      // Both handlers settle the observed promise even when cleanup itself fails.
      void promise.then(
        (value) => finish({ value }),
        (error) => finish({ error }),
      );
      try {
        if (signal?.aborted) {
          abort();
          return;
        }
        signal?.addEventListener('abort', abort, { once: true });
        if (timeout !== null && timeout !== Infinity)
          timer = setTimeout(() => finish({ value: null }), Math.max(timeout, 0) * 1000);
      } catch (error) {
        finish({ error });
      }
    });
    return this.retain(observed);
  }
  private own<T>(work: () => T | Promise<T>): Promise<T> {
    const result = deferred<T>();
    this.retain(result.promise);
    void result.promise.then(
      () => {},
      (error) => {
        if (!this.cleanup_failures.has(error) && !is_shutdown_business_cancellation(error))
          console.error('Executor mailbox operation failed.', error);
      },
    );
    try {
      result.resolve(work());
    } catch (error) {
      result.reject(error);
    }
    return result.promise;
  }
  private put<T extends WireValue<'MailboxCommand'> | WireValue<'CommandOutcome'>>(
    insert: (tx: TransactionSql, now: number) => T,
    silent: boolean,
    options: { signal?: AbortSignal },
  ): Promise<T> {
    this.admit(options.signal);
    const operation = this.own(() => {
      const { placed, high } = this.database.write(
        (tx) => {
          const placed = insert(tx, this.clock());
          return {
            placed,
            high: 'command_sequence' in placed ? (this.high ?? data.high_sequence(tx)) : null,
          };
        },
        silent ? { changeSignal: 'silent' } : undefined,
      );
      return Promise.resolve().then(() => {
        if ('command_sequence' in placed) {
          const sequence = integer_value(placed.command_sequence);
          let highest = high!;
          if (this.high !== null && this.high > highest) highest = this.high;
          this.high = highest > sequence ? highest : sequence;
          this.put_event.resolve(undefined);
          this.put_event = deferred<void>();
        }
        return placed;
      });
    });
    return this.observe(operation, options.signal);
  }
  async put_child_command(
    command: WireValue<'ChildCommand'>,
    options: { signal?: AbortSignal } = {},
  ): Promise<WireValue<'MailboxCommand'> | WireValue<'CommandOutcome'>> {
    return this.put((tx, now) => data.insert_child_command(tx, command, now), false, options);
  }
  async put_model_list_request(
    request: WireValue<'ModelListRequest'>,
    options: { signal?: AbortSignal } = {},
  ): Promise<WireValue<'MailboxCommand'>> {
    return this.put((tx, now) => data.insert_model_list_request(tx, request, now), true, options);
  }
  async poll(
    identity: WireValue<'ExecutorIdentity'>,
    after: MailboxCounter,
    wait_seconds: number,
    options: { signal?: AbortSignal } = {},
  ): Promise<WireValue<'MailboxPollReply'>> {
    this.admit(options.signal);
    return this.own(async () => {
      const deadline = performance.now() / 1000 + Math.max(wait_seconds, 0);
      const arrived = this.clock();
      while (!this.closed) {
        options.signal?.throwIfAborted();
        const wake = this.put_event.promise;
        const { reply, settled } = this.database.write(
          (tx) =>
            data.fetch(
              tx,
              identity.executor_incarnation,
              integer_value(after),
              this.clock(),
              arrived,
            ),
          { changeSignal: 'silent' },
        );
        this.publish_settlements(settled);
        const remaining = deadline - performance.now() / 1000;
        if (reply === null) return { commands: [] };
        if (reply.commands.length || remaining <= 0) return reply;
        if (this.closed) break;
        await this.observe(wake, options.signal, remaining);
      }
      return { commands: [] };
    });
  }
  async record_results(
    identity: WireValue<'ExecutorIdentity'>,
    report: WireValue<'CommandOutcomeReport'>,
    options: { signal?: AbortSignal } = {},
  ): Promise<WireValue<'CommandOutcomeReply'>> {
    this.admit(options.signal);
    const operation = this.own(async () => {
      const { reply, settled } = this.database.write((tx) =>
        data.record(tx, identity.executor_incarnation, report.outcomes, this.clock()),
      );
      await Promise.resolve();
      this.publish_settlements(settled);
      return reply;
    });
    return this.observe(operation, options.signal);
  }
  has_waiter(command_id: string): boolean {
    return Boolean(this.waiters.get(command_id)?.size);
  }
  async wait_result(
    command_id: string,
    timeout_seconds: number | null = null,
    options: { signal?: AbortSignal } = {},
  ): Promise<WireValue<'CommandResult'> | null> {
    this.admit(options.signal);
    return this.own(async () => {
      const future = deferred<WireValue<'CommandResult'>>();
      const waiting = this.waiters.get(command_id) ?? new Set();
      waiting.add(future);
      this.waiters.set(command_id, waiting);
      try {
        const row = this.database.read((sql) => data.settled_row(sql, command_id));
        if (row?.result !== null && row?.result !== undefined) future.resolve(row.result);
        return await this.observe(future.promise, options.signal, timeout_seconds);
      } finally {
        waiting.delete(future);
        if (!waiting.size) this.waiters.delete(command_id);
      }
    });
  }
  publish_settlements(settlements: MailboxSettlements): void {
    for (const [command, result] of settlements) {
      for (const future of this.waiters.get(command.command_id) ?? []) future.resolve(result);
      this.settled_listener?.(command, result);
    }
  }
  async withdraw_waiting(
    command_id: string,
    reason: CommandSettlementReason,
    options: { signal?: AbortSignal } = {},
  ): Promise<boolean> {
    this.admit(options.signal);
    const operation = this.own(async () => {
      const settled = this.database.write(
        (tx) => data.withdraw(tx, command_id, reason, this.clock()),
        { changeSignal: 'silent' },
      );
      await Promise.resolve();
      this.publish_settlements(settled);
      return Boolean(settled.length);
    });
    return this.observe(operation, options.signal);
  }
  async finish_by_proof(
    command_id: string,
    outcome: WireValue<'CommandOutcome'>,
    options: { signal?: AbortSignal } = {},
  ): Promise<void> {
    this.admit(options.signal);
    const operation = this.own(async () => {
      const settled = this.database.write((tx) =>
        data.finish_by_proof(tx, command_id, outcome, this.clock()),
      );
      await Promise.resolve();
      this.publish_settlements(settled);
    });
    return this.observe(operation, options.signal);
  }
  replace_incarnation_in_transaction(
    tx: TransactionSql,
    options: { replaced: string; current: string; now_ms: number },
  ): MailboxSettlements {
    this.admit();
    return data.replace_incarnation_in_transaction(
      tx,
      options.replaced,
      options.current,
      options.now_ms,
    );
  }
  high_sequence(): MailboxCounter {
    this.admit();
    this.high ??= this.database.read(data.high_sequence);
    return normalize_integer(this.high);
  }
  private read<T>(work: (sql: ReadSql) => T): T {
    this.admit();
    return this.database.read(work);
  }
  command_row(command_id: string): MailboxRow | null {
    return this.read((sql) => data.command_row(sql, command_id));
  }
  open_command_ids_of(child: WireValue<'ChildReference'>): readonly string[] {
    return this.read((sql) => data.open_command_ids_of(sql, child));
  }
  prompt_withdrawn_at_replacement(
    child: WireValue<'ChildReference'>,
    turn: MailboxCounter,
  ): WireValue<'WritePrompt'> | null {
    return this.read((sql) =>
      data.prompt_withdrawn_at_replacement(sql, child, integer_value(turn)),
    );
  }
  prompts_written_again(): readonly PromptWrittenAgain[] {
    return this.read(data.prompts_written_again);
  }
  late_steers_left_by_a_restart(): readonly LateSteerAfterRestart[] {
    return this.read(data.late_steers_left_by_a_restart);
  }
  start_of(child: WireValue<'ChildReference'>): WireValue<'ResolvedConversationStart'> | null {
    return this.read((sql) => data.start_of(sql, child));
  }
  command_for_input(command_id: string): WireValue<'ChildCommand'> | null {
    return this.read((sql) => data.command_for_input(sql, command_id));
  }
  stall_source(): StallSource {
    return {
      tracked_waits: (sql, now) => {
        this.admit();
        return data.tracked_waits(sql, now);
      },
    };
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.closed) {
      // Publish the fence and retain every first-stop promise before callbacks can reenter.
      this.closed = true;
      const pending = [...this.operations];
      this.put_event.resolve(undefined);
      for (const waiting of this.waiters.values())
        for (const future of waiting) future.reject(this.closed_waiter_error);
      this.waiters.clear();
      void this.drain(pending).then(this.resource_join.resolve, this.resource_join.reject);
    }
    return observe_shutdown(
      this.resource_join.promise,
      options,
      'Executor mailbox work remains unfinished at the shutdown deadline.',
    );
  }
  private async drain(first: readonly Promise<unknown>[]): Promise<void> {
    const failures = new Set<unknown>();
    const seen = new Set<Promise<unknown>>();
    let pending = first;
    while (pending.length) {
      for (const operation of pending) seen.add(operation);
      for (const result of await Promise.allSettled(pending))
        if (result.status === 'rejected' && !is_shutdown_business_cancellation(result.reason))
          failures.add(result.reason);
      pending = [...this.operations].filter((operation) => !seen.has(operation));
    }
    // Cleanup failures remain failures even when their inner error is a typed cancellation.
    for (const failure of this.cleanup_failures) failures.add(failure);
    if (failures.size) throw new AggregateError([...failures], 'Executor mailbox shutdown failed.');
  }
  settled(): Promise<void> {
    return this.resource_join.promise;
  }
}
