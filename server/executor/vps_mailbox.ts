/** Durable rows own commands. This owner supplies only waits and commit announcements. */
import type { Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { JsonNumber } from '../core/http/numeric_json.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { StallSource } from '../stalls/contracts.ts';
import type {
  CommandSettlementReason,
  MailboxCounter,
  MailboxRow,
  MailboxSettlements,
  SettledListener,
  WireValue,
} from './contracts.ts';
import { now_ms } from './clock.ts';
import * as data from './data/mailbox.ts';

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, failed) => { resolve = done; reject = failed; });
  return { promise, resolve, reject };
}
function wait<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T>;
function wait<T>(promise: Promise<T>, signal: AbortSignal | undefined, timeout: number | null): Promise<T | null>;
function wait<T>(promise: Promise<T>, signal?: AbortSignal, timeout: number | null = null): Promise<T | null> {
  return new Promise<T | null>((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const abort = () => { cleanup(); reject(signal!.reason); };
    const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); };
    promise.then(
      value => { cleanup(); resolve(value); },
      error => { cleanup(); reject(error); },
    );
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener('abort', abort, { once: true });
    if (timeout !== null && timeout !== Infinity)
      timer = setTimeout(() => { cleanup(); resolve(null); }, Math.max(timeout, 0) * 1000);
  });
}

export class SqliteCommandMailbox {
  settled_listener: SettledListener | null = null;
  private readonly waiters = new Map<string, Set<Deferred<WireValue<'CommandResult'>>>>();
  private readonly operations = new Set<Promise<unknown>>();
  private readonly closed_waiter_error = new DOMException('Executor mailbox is closed.', 'AbortError');
  private put_event = deferred<void>();
  private high: bigint | null = null;
  private closed = false;
  private readonly clock: () => number;

  constructor(private readonly database: Database, options: { clock?: () => number } = {}) {
    this.clock = options.clock ?? now_ms;
  }
  private admit(signal?: AbortSignal): void {
    if (this.closed) throw new ServerLifecycleError('Executor mailbox is closed.');
    signal?.throwIfAborted();
  }
  private own<T>(work: () => T | Promise<T>, signal?: AbortSignal): Promise<T> {
    const result = deferred<T>();
    this.operations.add(result.promise);
    void result.promise.then(
      () => this.operations.delete(result.promise),
      error => {
        this.operations.delete(result.promise);
        if (error !== this.closed_waiter_error && !(signal?.aborted && error === signal.reason))
          console.error('Executor mailbox operation failed.', error);
      },
    );
    try { result.resolve(work() as T); } catch (error) { result.reject(error); }
    return result.promise;
  }
  private put<T extends WireValue<'MailboxCommand'> | WireValue<'CommandOutcome'>>(
    insert: (tx: TransactionSql, now: number) => T,
    silent: boolean,
    options: { signal?: AbortSignal },
  ): Promise<T> {
    this.admit(options.signal);
    const operation = this.own(() => {
      const { placed, high } = this.database.write(tx => {
        const placed = insert(tx, this.clock());
        return { placed, high: 'command_sequence' in placed ? this.high ?? data.high_sequence(tx) : null };
      }, silent ? { changeSignal: 'silent' } : undefined);
      return Promise.resolve().then(() => {
        if ('command_sequence' in placed) {
          const sequence = data.counter_integer(placed.command_sequence);
          this.high = high! > sequence ? high! : sequence;
          this.put_event.resolve();
          this.put_event = deferred<void>();
        }
        return placed;
      });
    });
    return wait(operation, options.signal);
  }
  async put_child_command(command: WireValue<'ChildCommand'>, options: { signal?: AbortSignal } = {}): Promise<WireValue<'MailboxCommand'> | WireValue<'CommandOutcome'>> {
    return this.put((tx, now) => data.insert_child_command(tx, command, now), false, options);
  }
  async put_model_list_request(request: WireValue<'ModelListRequest'>, options: { signal?: AbortSignal } = {}): Promise<WireValue<'MailboxCommand'>> {
    return this.put((tx, now) => data.insert_model_list_request(tx, request, now), true, options);
  }
  async poll(identity: WireValue<'ExecutorIdentity'>, after: MailboxCounter, wait_seconds: number, options: { signal?: AbortSignal } = {}): Promise<WireValue<'MailboxPollReply'>> {
    this.admit(options.signal);
    return this.own(async () => {
      const deadline = performance.now() / 1000 + Math.max(wait_seconds, 0);
      const arrived = this.clock();
      while (!this.closed) {
        options.signal?.throwIfAborted();
        const wake = this.put_event.promise;
        const { reply, settled } = this.database.write(tx => data.fetch(tx, identity.executor_incarnation, data.counter_integer(after), this.clock(), arrived), { changeSignal: 'silent' });
        this.publish_settlements(settled);
        const remaining = deadline - performance.now() / 1000;
        if (reply === null) return { commands: [] };
        if (reply.commands.length || remaining <= 0) return reply;
        if (this.closed) break;
        await wait(wake, options.signal, remaining);
      }
      return { commands: [] };
    }, options.signal);
  }
  async record_results(identity: WireValue<'ExecutorIdentity'>, report: WireValue<'CommandOutcomeReport'>, options: { signal?: AbortSignal } = {}): Promise<WireValue<'CommandOutcomeReply'>> {
    this.admit(options.signal);
    const operation = this.own(async () => {
      const { reply, settled } = this.database.write(tx => data.record(tx, identity.executor_incarnation, report.outcomes, this.clock()));
      await Promise.resolve();
      this.publish_settlements(settled);
      return reply;
    });
    return wait(operation, options.signal);
  }
  has_waiter(command_id: string): boolean { return Boolean(this.waiters.get(command_id)?.size); }
  async wait_result(command_id: string, timeout_seconds: number | null = null, options: { signal?: AbortSignal } = {}): Promise<WireValue<'CommandResult'> | null> {
    this.admit(options.signal);
    return this.own(async () => {
      const future = deferred<WireValue<'CommandResult'>>();
      const waiting = this.waiters.get(command_id) ?? new Set();
      waiting.add(future);
      this.waiters.set(command_id, waiting);
      try {
        const row = this.database.read(sql => data.settled_row(sql, command_id));
        if (row?.result !== null && row?.result !== undefined) future.resolve(row.result);
        return await wait(future.promise, options.signal, timeout_seconds);
      } finally {
        waiting.delete(future);
        if (!waiting.size) this.waiters.delete(command_id);
      }
    }, options.signal);
  }
  publish_settlements(settlements: MailboxSettlements): void {
    for (const [command, result] of settlements) {
      for (const future of this.waiters.get(command.command_id) ?? []) future.resolve(result);
      this.settled_listener?.(command, result);
    }
  }
  async withdraw_waiting(command_id: string, reason: CommandSettlementReason, options: { signal?: AbortSignal } = {}): Promise<boolean> {
    this.admit(options.signal);
    const operation = this.own(async () => {
      const settled = this.database.write(tx => data.withdraw(tx, command_id, reason, this.clock()), { changeSignal: 'silent' });
      await Promise.resolve();
      this.publish_settlements(settled);
      return Boolean(settled.length);
    });
    return wait(operation, options.signal);
  }
  async finish_by_proof(command_id: string, outcome: WireValue<'CommandOutcome'>, options: { signal?: AbortSignal } = {}): Promise<void> {
    this.admit(options.signal);
    const operation = this.own(async () => {
      const settled = this.database.write(tx => data.finish_by_proof(tx, command_id, outcome, this.clock()));
      await Promise.resolve();
      this.publish_settlements(settled);
    });
    return wait(operation, options.signal);
  }
  replace_incarnation_in_transaction(tx: TransactionSql, options: { replaced: string; current: string; now_ms: number }): MailboxSettlements {
    this.admit();
    return data.replace_incarnation_in_transaction(tx, options.replaced, options.current, options.now_ms);
  }
  high_sequence(): MailboxCounter {
    this.admit();
    this.high ??= this.database.read(data.high_sequence);
    return new JsonNumber(this.high.toString());
  }
  private read<T>(work: (sql: ReadSql) => T): T { this.admit(); return this.database.read(work); }
  command_row(command_id: string): MailboxRow | null { return this.read(sql => data.command_row(sql, command_id)); }
  open_command_ids_of(child: WireValue<'ChildReference'>): readonly string[] { return this.read(sql => data.open_command_ids_of(sql, child)); }
  prompt_withdrawn_at_replacement(child: WireValue<'ChildReference'>, turn: MailboxCounter): ReturnType<typeof data.prompt_withdrawn_at_replacement> { return this.read(sql => data.prompt_withdrawn_at_replacement(sql, child, turn)); }
  prompts_written_again(): ReturnType<typeof data.prompts_written_again> { return this.read(data.prompts_written_again); }
  late_steers_left_by_a_restart(): ReturnType<typeof data.late_steers_left_by_a_restart> { return this.read(data.late_steers_left_by_a_restart); }
  start_of(child: WireValue<'ChildReference'>): ReturnType<typeof data.start_of> { return this.read(sql => data.start_of(sql, child)); }
  command_for_input(command_id: string): WireValue<'ChildCommand'> | null { return this.read(sql => data.command_for_input(sql, command_id)); }
  stall_source(): StallSource { return { tracked_waits: (sql, now) => { this.admit(); return data.tracked_waits(sql, now); } }; }
  close(options: Partial<StopOptions> = {}): Promise<void> {
    this.closed = true;
    this.put_event.resolve();
    for (const waiting of this.waiters.values())
      for (const future of waiting) future.reject(this.closed_waiter_error);
    this.waiters.clear();
    return observe_shutdown(this.settled(), options, 'Executor mailbox work remains unfinished at the shutdown deadline.');
  }
  async settled(): Promise<void> {
    while (this.operations.size) await Promise.allSettled([...this.operations]);
  }
}
