/** Retained Mac children and restart recovery, with durable assignments and owned waits. */
import { same_reference } from './child_reference.ts';
import { randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { setTimeout as pause } from 'node:timers/promises';
import { BackendSpawnFailed } from '../conversation/backends/contracts.ts';
import type {
  BackendChildFactory,
  BackendEventSink,
  ChildReference,
  RetainedBackendChildFactory,
  RetainedBackendRun,
} from '../conversation/backends/contracts.ts';
import { ConversationRoleMaterials, ResolvedConversationStart } from '../conversation/contracts.ts';
import type { UndeliveredTurnPrompt } from '../conversation/record/contracts.ts';
import { close_mac_interruption_of_turn } from '../conversation/deploy_interruptions/data.ts';
import { admit_remote_integer, admit_remote_turn } from '../conversation/runtime/remote_turn.ts';
import { observe_shutdown, is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import type { Database } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { supports_executor_versions } from '../execution_identity.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { StallSource } from '../stalls/contracts.ts';
import { WAITS } from '../stalls/registry.ts';
import { execution_unavailable_reason } from '../tickets/locale_configuration.ts';
import { now_ms } from './clock.ts';
import { native_message_content } from './backend_events.ts';
import { CommandSettlementReason } from './contracts.ts';
import type { WireValue } from './contracts.ts';
import { ExecutorStore } from './data/runs.ts';
import { steer_never_arrived } from './data/mailbox.ts';
import { integer_value, ValueError } from './protocol.ts';
import {
  STEER_DELIVERY_LIMIT_SECONDS,
  DRAIN_RETRY_INITIAL_SECONDS,
  DRAIN_RETRY_MAX_SECONDS,
} from './configuration.ts';
import { ticket_id_of } from './routing.ts';
import type { MacRunConversations } from './run_contracts.ts';
import { read_canonical_skill_snapshot } from './data/skills.ts';
import { SqliteCommandMailbox } from './vps_mailbox.ts';
import { MacPresenceService } from './vps_presence.ts';
import { LateSteer, MacRemoteChild } from './vps_remote_child.ts';
interface Drain {
  reference: ChildReference;
  replaced_by: string;
  drained: Promise<void>;
  resolve(): void;
  reached: boolean;
}
const MAILBOX_SETTLEMENTS = new Set<string>([
  CommandSettlementReason.executor_process_replaced,
  CommandSettlementReason.steer_turn_not_running,
  CommandSettlementReason.steer_delivery_limit_passed,
  CommandSettlementReason.delivery_withdrawn,
]);
function native_start(
  value: WireValue<'ResolvedConversationStart'> | null,
): ResolvedConversationStart | null {
  return value === null
    ? null
    : new ResolvedConversationStart({
        ...value,
        role_materials:
          value.role_materials === null
            ? null
            : new ConversationRoleMaterials(value.role_materials),
      });
}
function undelivered(
  turn: RetainedBackendRun['turn'],
  prompt: WireValue<'WritePrompt'>,
): UndeliveredTurnPrompt {
  return {
    turn_number: admit_remote_turn(turn).turn_number,
    content: native_message_content(prompt.content),
    sender_content: native_message_content(prompt.sender_content),
    sender_label: prompt.sender_label,
    sender_message_count: admit_remote_integer(
      prompt.sender_message_count ?? 1,
      'Remote sender message count',
    ),
    mode: prompt.mode,
    model_change: prompt.model_change ?? null,
    reasoning_effort_change: prompt.reasoning_effort_change ?? null,
    reasoning_effort_is_set: prompt.reasoning_effort_is_set ?? false,
    automatic_compaction: prompt.automatic_compaction ?? false,
  };
}
export class MacRuns {
  readonly store: ExecutorStore;
  readonly presence: MacPresenceService;
  readonly mailbox: SqliteCommandMailbox;
  conversations: MacRunConversations | null = null;
  readonly children = new Map<string, MacRemoteChild>();
  private readonly database: Database;
  private readonly drains = new Map<string, Drain>();
  private readonly tasks = new Set<Promise<unknown>>();
  private readonly failures = new Set<unknown>();
  private readonly cancellation = new AbortController();
  private sealed = false;
  private stop_task: Promise<void> | null = null;
  private readonly actual_settlement: Promise<void>;
  private resolve_settlement!: () => void;
  private reject_settlement!: (error: unknown) => void;
  constructor(database: Database, presence: MacPresenceService, mailbox: SqliteCommandMailbox) {
    this.database = database;
    this.presence = presence;
    this.mailbox = mailbox;
    this.store = new ExecutorStore(database);
    this.actual_settlement = new Promise<void>((resolve, reject) => {
      this.resolve_settlement = resolve;
      this.reject_settlement = reject;
    });
    void this.actual_settlement.catch(() => {});
    mailbox.settled_listener = this.command_settled;
  }
  private admit(): void {
    if (this.sealed) throw new ServerLifecycleError('Executor runs are closed.');
  }
  private continue_work(): void {
    if (this.sealed) throw this.cancellation.signal.reason;
  }
  private own<T>(work: () => Promise<T>, background = false): Promise<T> {
    if (this.sealed) return Promise.reject(new ServerLifecycleError('Executor runs are closed.'));
    const task = Promise.resolve().then(() => {
      this.continue_work();
      return work();
    });
    this.tasks.add(task);
    void task.then(
      () => this.tasks.delete(task),
      (error) => {
        this.tasks.delete(task);
        if (background && !is_shutdown_business_cancellation(error)) {
          this.failures.add(error);
          console.warn('An owned executor run task failed.', error);
        }
      },
    );
    return task;
  }
  require_conversations(): MacRunConversations {
    if (this.conversations === null)
      throw new ServerLifecycleError('Executor runs require attached conversations.');
    return this.conversations;
  }
  create(options: Parameters<BackendChildFactory>[0]): MacRemoteChild {
    this.admit();
    if (!ticket_id_of(options.resolved_start))
      throw new BackendSpawnFailed('Mac runs require a Ticket Worker identity');
    const reference = this.assign_child(options.resolved_start);
    const child = this.make_child(reference, {
      event_sink: options.event_sink,
      initial_history_complete: true,
    });
    this.children.set(reference.child_id, child);
    return child;
  }
  as_factory(): RetainedBackendChildFactory {
    return Object.assign((options: Parameters<BackendChildFactory>[0]) => this.create(options), {
      recover: this.recover.bind(this),
      retained_runs: this.retained_runs.bind(this),
    });
  }
  retained_runs() {
    this.admit();
    return this.store.retained_runs();
  }
  recover(
    reference: ChildReference,
    options: { event_sink?: BackendEventSink | null } = {},
  ): MacRemoteChild | null {
    this.admit();
    if (this.store.child_assignment(reference) === null) return null;
    const child = this.make_child(reference, {
      event_sink: options.event_sink ?? null,
      started_on: native_start(this.mailbox.start_of(reference)),
    });
    this.children.set(reference.child_id, child);
    return child;
  }
  private read_skill_snapshot = (
    start: ResolvedConversationStart,
  ): WireValue<'CanonicalSkillSnapshot'> => {
    this.admit();
    const ticket_id = ticket_id_of(start);
    if (!ticket_id)
      throw new BackendSpawnFailed('Canonical skill bootstrap requires a Worker Ticket identity');
    return this.database.read((sql) => read_canonical_skill_snapshot(sql, ticket_id));
  };
  assign_child(start: ResolvedConversationStart): ChildReference {
    this.admit();
    const record = this.presence.record(),
      identity = this.presence.current_identity();
    if (
      record === null ||
      identity === null ||
      record.refusal !== null ||
      !supports_executor_versions(identity.protocol_version, identity.spool_format_version)
    )
      throw new BackendSpawnFailed(
        this.database.read((sql) => execution_unavailable_reason(sql, 'macos')),
      );
    const ticket_id = ticket_id_of(start);
    if (!ticket_id) throw new BackendSpawnFailed('Mac runs require a Ticket Worker identity');
    const child = {
      executor_incarnation: identity.executor_incarnation,
      child_id: randomUUID(),
      conversation_id: start.conversation_id,
    };
    this.store.register_child(child, 'ticket', ticket_id, now_ms());
    return child;
  }
  private make_child(
    reference: ChildReference,
    options: ConstructorParameters<typeof MacRemoteChild>[3] = {},
  ): MacRemoteChild {
    return new MacRemoteChild(reference, this, this.read_skill_snapshot, {
      initial_history_complete: false,
      ...options,
    });
  }
  retire_child(reference: ChildReference): void {
    const proxy = this.children.get(reference.child_id);
    if (proxy !== undefined && same_reference(proxy.reference, reference))
      this.children.delete(reference.child_id);
  }
  mac_is_present(): boolean {
    return this.presence.is_present();
  }
  put(command: WireValue<'ChildCommand'>): Promise<boolean> {
    return this.own(
      async () =>
        'command_sequence' in
        (await this.mailbox.put_child_command(command, { signal: this.cancellation.signal })),
    );
  }
  deliver_steer(
    command: WireValue<'ChildCommand'>,
  ): Promise<WireValue<'CommandOutcome'> | LateSteer | null> {
    return this.own(async () => {
      if (!(await this.put(command))) return null;
      let result = await this.mailbox.wait_result(
        command.command_id,
        STEER_DELIVERY_LIMIT_SECONDS,
        { signal: this.cancellation.signal },
      );
      this.continue_work();
      if (result === null) {
        if (
          await this.mailbox.withdraw_waiting(
            command.command_id,
            CommandSettlementReason.steer_delivery_limit_passed,
          )
        )
          return null;
        this.continue_work();
        result = await this.mailbox.wait_result(command.command_id, 0, {
          signal: this.cancellation.signal,
        });
        this.continue_work();
        if (result === null)
          return new LateSteer(this.own(() => this.steer_not_delivered(command.command_id), true));
      }
      const outcome = result.child_outcome ?? null;
      return outcome === null ||
        (outcome.disposition === 'refused' &&
          typeof outcome.result.reason === 'string' &&
          MAILBOX_SETTLEMENTS.has(outcome.result.reason))
        ? null
        : outcome;
    });
  }
  private async steer_not_delivered(command_id: string): Promise<boolean> {
    const result = await this.mailbox.wait_result(command_id, null, {
      signal: this.cancellation.signal,
    });
    return steer_never_arrived(result?.child_outcome ?? null);
  }
  stop(command: WireValue<'ChildCommand'>): Promise<WireValue<'CommandOutcome'>>;
  stop(options: StopOptions): Promise<void>;
  stop(
    value: WireValue<'ChildCommand'> | StopOptions,
  ): Promise<WireValue<'CommandOutcome'>> | Promise<void> {
    if ('deadline' in value) return this.stop_owner(value);
    const command = value;
    return this.own(async () => {
      const replaced: WireValue<'CommandOutcome'> = {
        command_id: command.command_id,
        child: command.child,
        disposition: 'succeeded',
        result: { reason: CommandSettlementReason.executor_process_replaced },
      };
      if (!(await this.put(command))) {
        await this.closed_without_stop(command.child);
        return replaced;
      }
      const result = await this.mailbox.wait_result(command.command_id, null, {
        signal: this.cancellation.signal,
      });
      this.continue_work();
      const outcome = result?.child_outcome;
      if (outcome == null) throw new Error('a stop settled without its outcome');
      if (outcome.result.reason === CommandSettlementReason.executor_process_replaced) {
        await this.closed_without_stop(command.child);
        return replaced;
      }
      if (
        outcome.disposition === 'succeeded' &&
        !this.store.has_closed_child_assignment(command.child)
      )
        this.store.close_child(command.child, now_ms());
      return outcome;
    });
  }
  private async closed_without_stop(child: ChildReference): Promise<void> {
    this.continue_work();
    if (this.store.has_closed_child_assignment(child)) return;
    const current = this.presence.current_identity();
    if (current === null || current.executor_incarnation === child.executor_incarnation) return;
    const drain = this.start_drain(child, current.executor_incarnation);
    await drain.drained;
    this.continue_work();
    if (drain.reached) this.store.close_child(child, now_ms());
  }
  save_answer(command: WireValue<'ChildCommand'>): Promise<boolean> {
    return this.own(async () => {
      const conversations = this.require_conversations();
      let inserted = false;
      if (command.turn !== null)
        await conversations.emit_answer(admit_remote_turn(command.turn), async () => {
          this.continue_work();
          inserted = await this.put(command);
        });
      return inserted;
    });
  }
  cancel_initial_delivery(child: ChildReference, command_ids: readonly string[]): Promise<boolean> {
    return this.own(async () => {
      let may_have_sent = false;
      for (const command_id of command_ids) {
        this.continue_work();
        await this.mailbox.withdraw_waiting(command_id, CommandSettlementReason.delivery_withdrawn);
        this.continue_work();
        const row = this.mailbox.command_row(command_id);
        const command = row?.command.child_command;
        if (command == null) continue;
        if (!same_reference(command.child, child))
          throw new ValueError('initial command belongs to another child');
        const outcome = row?.result?.child_outcome;
        if (
          command.operation === 'write_prompt' &&
          !(outcome != null && outcome.disposition === 'refused')
        )
          may_have_sent = true;
      }
      return may_have_sent;
    });
  }
  private command_settled = (
    command: WireValue<'MailboxCommand'>,
    result: WireValue<'CommandResult'>,
  ): void => {
    const child = command.child_command,
      outcome = result.child_outcome;
    if (
      this.sealed ||
      child == null ||
      outcome == null ||
      child.operation !== 'cancel_running_turn' ||
      outcome.disposition === 'succeeded' ||
      outcome.result.reason === CommandSettlementReason.executor_process_replaced
    )
      return;
    void this.own(() => this.retire_after_failed_cancel(child.child), true);
  };
  private async retire_after_failed_cancel(reference: ChildReference): Promise<void> {
    const conversations = this.require_conversations();
    const wait = WAITS.track('mac_cancel_retirement', reference.child_id, {
      detail: 'Retiring a Mac child whose cancel failed',
      conversation_id: reference.conversation_id,
    });
    try {
      let proxy = this.children.get(reference.child_id);
      if (proxy === undefined || !same_reference(proxy.reference, reference)) {
        if (this.store.child_assignment(reference) === null) return;
        proxy = this.make_child(reference);
        this.children.set(reference.child_id, proxy);
      }
      try {
        await proxy.stop();
      } catch (error) {
        if (is_shutdown_business_cancellation(error)) throw error;
        console.warn(
          `A Mac child whose cancel failed did not stop: conversation=${reference.conversation_id}`,
          error,
        );
        return;
      }
      this.continue_work();
      await conversations.confirm_remote_child_termination(reference);
    } finally {
      wait.close();
    }
  }
  authority(child: ChildReference): Principal {
    const binding = this.store.child_assignment(child);
    if (binding === null)
      throw new PlannerError('agent_forbidden', 'child has no VPS assignment', {});
    return { kind: binding[0] as Principal['kind'], id: binding[1] };
  }
  owns_conversation(conversation_id: string): boolean {
    return this.store
      .retained_runs()
      .some(
        (run) => run.reference.conversation_id === conversation_id && !run.retirement_confirmed,
      );
  }
  validate_stop(outcome: WireValue<'CommandOutcome'>): void {
    const row = this.mailbox.command_row(outcome.command_id);
    if (row !== null) {
      const command = row.command.child_command;
      if (
        command == null ||
        command.operation !== 'stop' ||
        !same_reference(command.child, outcome.child)
      )
        throw new ValueError('child_stopped conflicts with its command');
      if (
        this.store.child_assignment(outcome.child) === null &&
        !this.store.has_closed_child_assignment(outcome.child)
      )
        this.authority(outcome.child);
    } else this.authority(outcome.child);
    if (outcome.disposition !== 'succeeded')
      throw new ValueError('child_stopped requires successful exact-child stop');
  }
  accept_stop_receipt(
    outcome: WireValue<'CommandOutcome'>,
    receipt: WireValue<'AcceptanceReceipt'>,
  ): Promise<void> {
    return this.own(async () => {
      const conversations = this.require_conversations();
      if (
        receipt.result.stop_command_id !== outcome.command_id ||
        !isDeepStrictEqual(receipt.result.stopped_child, outcome.child)
      ) {
        if (!Object.hasOwn(receipt.result, 'stop_command_id'))
          await this.mailbox.finish_by_proof(outcome.command_id, outcome);
        return;
      }
      const awaited = this.mailbox.has_waiter(outcome.command_id);
      await this.mailbox.finish_by_proof(outcome.command_id, outcome);
      this.continue_work();
      if (!this.store.has_closed_child_assignment(outcome.child))
        this.store.close_child(outcome.child, now_ms());
      if (!awaited)
        void this.own(() => conversations.confirm_remote_child_termination(outcome.child), true);
    });
  }
  incarnation_replaced(_replaced: string, current: string): Promise<void> {
    return this.own(async () => {
      this.require_conversations();
      for (const reference of this.store.unresolved_runs_of_replaced_incarnations())
        this.start_drain(reference, current);
    });
  }
  resume_replacement_drains(): Promise<void> {
    return this.own(async () => {
      const conversations = this.require_conversations();
      for (const [
        conversation_id,
        sequence,
        command_id,
        never_arrived,
      ] of this.mailbox.late_steers_left_by_a_restart()) {
        this.continue_work();
        const native_sequence = admit_remote_integer(sequence, 'Remote steer sequence');
        const verdict =
          never_arrived === null
            ? this.own(() => this.steer_not_delivered(command_id), true)
            : Promise.resolve(never_arrived);
        await conversations.await_late_steer(conversation_id, native_sequence, verdict);
      }
      this.continue_work();
      const current = this.presence.current_identity();
      if (current === null) return;
      await this.incarnation_replaced('', current.executor_incarnation);
      this.continue_work();
      this.close_rewritten_interruptions();
      for (const [
        child,
        turn_number,
        prompt,
        written,
        started,
      ] of this.mailbox.prompts_written_again()) {
        if (!written)
          void this.own(
            () =>
              conversations.resume_undelivered_prompt(
                child,
                undelivered({ conversation_id: child.conversation_id, turn_number }, prompt),
                { start_needed: !started },
              ),
            true,
          );
      }
    });
  }
  private close_rewritten_interruptions(conversation_id: string | null = null): void {
    this.continue_work();
    for (const [child, turn_number, _prompt, written] of this.mailbox.prompts_written_again()) {
      if (written && (conversation_id === null || conversation_id === child.conversation_id))
        this.database.write((tx) =>
          close_mac_interruption_of_turn(
            tx,
            child.conversation_id,
            integer_value(turn_number),
            Math.floor(now_ms() / 1000),
            'Its message was written again to a fresh agent.',
          ),
        );
    }
  }
  private start_drain(reference: ChildReference, current: string): Drain {
    this.continue_work();
    this.require_conversations();
    const existing = this.drains.get(reference.conversation_id);
    if (existing !== undefined && same_reference(existing.reference, reference)) return existing;
    let resolve!: () => void;
    const drained = new Promise<void>((done) => {
      resolve = done;
    });
    const drain = { reference, replaced_by: current, drained, resolve, reached: false };
    this.drains.set(reference.conversation_id, drain);
    void this.own(() => this.drain(drain), true);
    return drain;
  }
  private async drain(drain: Drain): Promise<void> {
    this.require_conversations();
    const reference = drain.reference;
    try {
      await this.presence.wait_until_drained(reference.conversation_id, {
        signal: this.cancellation.signal,
      });
      this.continue_work();
      for (const command_id of this.mailbox.open_command_ids_of(reference)) {
        await this.mailbox.wait_result(command_id, null, { signal: this.cancellation.signal });
        this.continue_work();
      }
      drain.reached = true;
      drain.resolve();
      this.retire_child(reference);
      const wait = WAITS.track('vps_replacement_retiring', reference.conversation_id, {
        detail: "A replaced Mac process's turn is not yet interrupted and recovered",
        conversation_id: reference.conversation_id,
      });
      try {
        await this.retry('retire', reference, () =>
          this.retire_replaced(reference, drain.replaced_by),
        );
      } finally {
        wait.close();
      }
      await this.retry('close', reference, async () => {
        this.store.close_child(reference, now_ms());
      });
    } finally {
      drain.resolve();
      if (this.drains.get(reference.conversation_id) === drain)
        this.drains.delete(reference.conversation_id);
    }
  }
  private async retire_replaced(reference: ChildReference, replaced_by: string): Promise<void> {
    const conversations = this.require_conversations();
    this.close_rewritten_interruptions(reference.conversation_id);
    const run = this.store.retained_runs().find((run) => same_reference(run.reference, reference));
    const prompt =
      run === undefined
        ? null
        : this.mailbox.prompt_withdrawn_at_replacement(reference, run.turn.turn_number);
    await conversations.retire_replaced_remote_child(reference, {
      replaced_by,
      undelivered_prompt:
        prompt === null || run === undefined ? null : undelivered(run.turn, prompt),
    });
  }
  private async retry(
    step: string,
    reference: ChildReference,
    work: () => Promise<unknown>,
  ): Promise<void> {
    let delay = DRAIN_RETRY_INITIAL_SECONDS;
    let failure_logged = false;
    while (true) {
      this.continue_work();
      try {
        await work();
        return;
      } catch (error) {
        if (is_shutdown_business_cancellation(error)) throw error;
        if (!failure_logged) {
          failure_logged = true;
          console.error(
            `The run of a replaced Mac process failed to ${step}, retrying: conversation=${reference.conversation_id}`,
            error,
          );
        }
      }
      try {
        await pause(delay * 1000, undefined, { signal: this.cancellation.signal });
      } catch (error) {
        // timers/promises wraps the signal reason in its own AbortError.
        if (this.cancellation.signal.aborted) throw this.cancellation.signal.reason;
        throw error;
      }
      delay = Math.min(delay * 2, DRAIN_RETRY_MAX_SECONDS);
    }
  }
  stall_source(): StallSource {
    return new ReplacementStallSource();
  }
  private stop_owner(options: StopOptions): Promise<void> {
    if (!this.sealed) {
      this.sealed = true;
      this.cancellation.abort(new DOMException('Executor runs are closed.', 'AbortError'));
      if (this.mailbox.settled_listener === this.command_settled)
        this.mailbox.settled_listener = null;
      for (const drain of this.drains.values()) drain.resolve();
      this.stop_task = this.finish_stop();
      void this.stop_task.then(this.resolve_settlement, this.reject_settlement);
    }
    return observe_shutdown(this.actual_settlement, options, 'Executor runs did not settle.', [
      'executor runs',
    ]);
  }
  private async finish_stop(): Promise<void> {
    const seen = new Set<Promise<unknown>>();
    let pending = [...this.tasks];
    while (pending.length) {
      for (const task of pending) seen.add(task);
      for (const result of await Promise.allSettled(pending))
        if (result.status === 'rejected' && !is_shutdown_business_cancellation(result.reason))
          this.failures.add(result.reason);
      pending = [...this.tasks].filter((task) => !seen.has(task));
    }
    if (this.failures.size)
      throw new AggregateError([...this.failures], 'Executor run shutdown failed.');
  }
  settled(): Promise<void> {
    return this.actual_settlement;
  }
}
export class ReplacementStallSource implements StallSource {
  tracked_waits(
    sql: Parameters<StallSource['tracked_waits']>[0],
    now: number,
  ): ReturnType<StallSource['tracked_waits']> {
    return sql
      .all<{ conversation_id: string; replaced_at_ms: number }>(
        "SELECT r.conversation_id,COALESCE(i.replaced_at_ms,i.first_leased_at_ms,?) AS replaced_at_ms FROM mac_runs AS r LEFT JOIN executor_incarnations AS i ON i.executor_incarnation=r.executor_incarnation LEFT JOIN mac_executor_presence AS p ON p.singleton=1 WHERE r.stop_state<>'closed' AND r.executor_incarnation IS NOT p.executor_incarnation ORDER BY r.rowid",
        [now],
      )
      .map((row) => ({
        kind: 'mac_replacement_pending',
        wait_key: row.conversation_id,
        conversation_id: row.conversation_id,
        detail: "A replaced Mac process's items are still draining",
        started_at_ms: row.replaced_at_ms,
      }));
  }
}
