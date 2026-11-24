import { PlannerError, type Principal } from '../core/contracts.ts';
import type {
  WorkspaceIdentity,
  RetirementTarget,
  MaybePromise,
} from '../ticket_workspaces/contracts.ts';
import type { CleanupResult } from '../worker_supervision/contracts.ts';
import { AutomaticStartAdmission } from './start_admission/index.ts';
import type { StartRequest } from './start_admission/contracts.ts';
/** Local process core. Registered as ConversationSystem after the turn-control slice lands. */
import { performance } from 'node:perf_hooks';
import type { BackendEventSink, BackendEventValue, TurnToken } from './backends/contracts.ts';
import type { BackendChild, ChildReference } from './backends/contracts.ts';
import type { WireValue } from '../executor/contracts.ts';
import { AnswerFate, HeldPrompt, PromptDeliveryQueued } from './contracts.ts';
import type {
  ConversationStartRequest,
  ConversationSystem,
  HeldPromptPromotionMode,
  OwnerSendProgress,
  PromptDeliveryFate,
  RecordMessageToOwnerOptions,
  SendOptions,
} from './contracts.ts';
import { ModelChangedEventPayload } from './events.ts';
import type { MessageContent } from './message_content.ts';
import { ConversationRecord } from './record/contracts.ts';
import type {
  RunningTurn,
  StoredConversationEvent,
  RecordCommitParticipant,
  AcceptedRecordEvent,
  UndeliveredTurnPrompt,
} from './record/contracts.ts';
import type { LifecycleFact } from './record/lifecycle.ts';
import type {
  CoreBackendEventSink,
  ConversationCoreOptions,
  DeliveryServices,
  Message,
  ReservedTurn,
  RuntimeMutation,
} from './runtime/contracts.ts';
import { take_backend_event } from './runtime/backend_events.ts';
import * as remote from './runtime/remote.ts';
import { admit_remote_turn } from './runtime/remote_turn.ts';
import * as controls from './runtime/controls.ts';
import * as answers from './runtime/answers.ts';
import * as start from './runtime/start.ts';
import * as maintenance from './runtime/maintenance.ts';
import * as send from './runtime/send.ts';
import * as delivery from './runtime/delivery.ts';
import * as backend_events from './runtime/backend_events.ts';
import * as child_lifecycle from './runtime/child_lifecycle.ts';
import * as turns from './runtime/turns.ts';
import * as queries from './runtime/queries.ts';
import * as update_preparation from './runtime/update_preparation.ts';
import * as system_shutdown from './runtime/system_shutdown.ts';
import { acquire_after_steers, acquire_settled } from './runtime/synchronization.ts';
import type { LockLease } from './runtime/synchronization.ts';
import { IdleChildJanitor, BackgroundTaskNotices } from './runtime/maintenance.ts';
import { save_attempt } from './runtime/save_attempt.ts';
import { ConversationState, own_settlement } from './runtime/state.ts';
import { WorkspaceAdmission } from './workspace_admission.ts';

function save_failure_detail(error: unknown): string {
  const code =
    error !== null && typeof error === 'object' && 'code' in error && typeof error.code === 'string'
      ? error.code
      : null;
  if (code === null) return `Save error: ${String(error)}`;
  const message = error instanceof Error ? error.message : '';
  return message ? `Save error ${code}: ${message}` : `Save error ${code}`;
}
export class SqliteProcessConversationSystem implements DeliveryServices, ConversationSystem {
  readonly options: ConversationCoreOptions;
  readonly workspace_admission = new WorkspaceAdmission();
  readonly start_admission: AutomaticStartAdmission;
  readonly retiring_workspace_tickets = new Set<string>();
  readonly retiring_workspace_conversations = new Set<string>();
  worker_cleanup_recheck:
    ((source: ChildReference, ticket_id: string) => Promise<CleanupResult>) | null = null;
  readonly deferred_start_conversations = new Set<string>();
  readonly deferred_start_preparing = new Set<string>();
  deferred_start_timer: ReturnType<typeof setTimeout> | null = null;
  readonly conversations = new Map<string, ConversationState>();
  shutting_down = false;
  shutdown_task: Promise<void> | null = null;
  readonly shutdown_controller = new AbortController();
  get shutdown_signal(): AbortSignal {
    return this.shutdown_controller.signal;
  }
  readonly native_waiters = new Set<() => void>();
  readonly native_settlements = new Set<Promise<void>>();
  readonly child_stop_ownership = new Map<
    import('./backends/contracts.ts').BackendChild,
    ConversationState
  >();
  idle_janitor: IdleChildJanitor | null = null;
  readonly background_task_notices: BackgroundTaskNotices;
  background_tasks_left_by_previous_process: ReadonlyMap<string, readonly string[]>;
  constructor(options: ConversationCoreOptions) {
    for (const entry of options.backend_registry.list())
      if (entry.kind !== 'live')
        throw new Error(`no backend child factory for ${entry.instance_id}: ${entry.reason}`);
    if (typeof options.execution_refusal !== 'function')
      throw new Error('Conversation execution admission is required');
    if (typeof options.delivery_model_refusal !== 'function')
      throw new Error('Conversation model admission is required');
    this.options = options;
    this.background_task_notices = new BackgroundTaskNotices(this);
    this.start_admission = new AutomaticStartAdmission({ clock: () => this.monotonic_now() });
    const adopted = remote.initialize_retained_runs(this);
    this.background_tasks_left_by_previous_process = new Map(
      [...options.store.read_live_background_task_ids()].filter(([id]) => !adopted.has(id)),
    );
  }
  tell_agents_of_background_tasks_lost_at_restart(): Promise<void> {
    return maintenance.tell_agents_of_background_tasks_lost_at_restart(this);
  }
  automatic_start_request(identity: string, conversation_id: string): Promise<StartRequest> {
    return start.automatic_start_request(this, identity, conversation_id);
  }
  accept_automatic_start(state: ConversationState, turn: TurnToken): Promise<void> {
    return start.accept_automatic_start(this, state, turn);
  }
  settle_automatic_ids(
    state: ConversationState,
    identities: Iterable<string | null>,
  ): Promise<void> {
    return start.settle_automatic_ids(this, state, identities);
  }
  unix_time_now(): number {
    return (this.options.unix_time_now ?? (() => Date.now() / 1000))();
  }
  monotonic_now(): number {
    return (this.options.monotonic_now ?? (() => performance.now() / 1000))();
  }
  require_runtime_ownership(): void {
    if (this.shutting_down) throw new Error('the conversation runtime is shut down');
    this.options.require_runtime_ownership?.();
  }
  conversation_state(conversation_id: string): ConversationState | null {
    this.require_runtime_ownership();
    return this.read_conversation_state(conversation_id);
  }
  /** An admitted executor outcome can settle until actual runtime ownership ends. */
  conversation_state_for_admitted_remote_acknowledgement(
    conversation_id: string,
  ): ConversationState | null {
    this.options.require_runtime_ownership?.();
    return this.read_conversation_state(conversation_id);
  }
  private read_conversation_state(conversation_id: string): ConversationState | null {
    const existing = this.conversations.get(conversation_id);
    if (existing) return existing;
    const record = this.options.store.read_conversation(conversation_id);
    if (!record) return null;
    const state = new ConversationState(record);
    remote.restore_retained_run(this, state);
    if (this.options.store.blocked_local_conversations.has(conversation_id))
      state.child_is_quarantined = true;
    this.conversations.set(conversation_id, state);
    return state;
  }
  ensure_local_custody(state: ConversationState): Promise<void> {
    return child_lifecycle.ensure_local_custody(this, state);
  }
  runtime_turn(conversation_id: string): RunningTurn | null {
    return queries.runtime_turn(this, conversation_id);
  }
  start_conversation(request: ConversationStartRequest): Promise<void> {
    return start.start_conversation(this, request);
  }
  persist<T>(
    state: ConversationState,
    work: () => T,
    mutations: readonly RuntimeMutation[] = [],
    retry_later_at?: number,
  ): Promise<T> {
    let failures = 0;
    let tracked: import('./runtime/contracts.ts').TrackedSaveRetry | null = null;
    const task = this.options.store.settle_recording(
      state.record.conversation_id,
      () => {
        try {
          return (this.options.save_attempt ?? save_attempt)(
            state.record.conversation_id,
            work,
            mutations,
          );
        } catch (error) {
          const code =
            error !== null && typeof error === 'object' && 'code' in error ? error.code : null;
          if (
            !(typeof code === 'string' && code.startsWith('SQLITE_BUSY')) &&
            !(error instanceof Error && error.name === 'AbortError')
          ) {
            failures += 1;
            const detail = `Stuck at write ${failures}`;
            if (tracked) tracked.set_detail(detail);
            else
              tracked =
                this.options.wait_registry?.track(
                  'vps_save_retrying',
                  state.record.conversation_id,
                  { detail, conversation_id: state.record.conversation_id },
                ) ?? null;
          }
          throw error;
        }
      },
      {
        shutdown_signal: this.shutdown_controller.signal,
        retry_later_at,
        single_attempt: state.save_failure !== null,
      },
    );
    return task
      .catch((error: unknown) => {
        if (!(error instanceof Error && error.name === 'AbortError'))
          this.note_save_gave_up(state, error, retry_later_at !== undefined);
        throw error;
      })
      .finally(() => tracked?.close());
  }
  private database_unusable_alarmed = false;
  /** A save gave up. Fail its conversation, and alarm once if the database is unusable. */
  private note_save_gave_up(state: ConversationState, error: unknown, bounded: boolean): void {
    if (this.shutting_down) return;
    const unusable = this.options.store.database_unusable_error;
    if (unusable !== null && !this.database_unusable_alarmed) {
      this.database_unusable_alarmed = true;
      console.error(
        'the database is unusable; conversations refuse new work until a restart',
        unusable,
      );
      this.options.wait_registry?.track('vps_save_failed', 'database', {
        detail: save_failure_detail(unusable),
        conversation_id: null,
      });
    }
    // A bounded caller owns its own failure while the database itself still works.
    if ((bounded && unusable === null) || state.save_failure !== null) return;
    const conversation_id = state.record.conversation_id;
    state.save_failure = error;
    console.error(
      `conversation ${conversation_id} save gave up; it refuses new work until a restart`,
      error,
    );
    this.options.wait_registry?.track('vps_save_failed', conversation_id, {
      detail: save_failure_detail(error),
      conversation_id,
    });
    // Owned, so quiescence and shutdown wait for the stop and the failed turn.
    void own_settlement(state, controls.fail_after_save_failure(this, state)).catch(
      (failure: unknown) =>
        console.error(
          `conversation ${conversation_id} could not stop after its save failed`,
          failure,
        ),
    );
  }
  /** Database and save failures refuse first, then the owner's execution admission. */
  execution_refusal(conversation_id: string): PlannerError | null {
    if (this.options.store.database_unusable_error !== null)
      return new PlannerError(
        'conversation_save_failed',
        'The database is unusable, so Panels refuses new work until a restart.',
      );
    if ((this.conversations.get(conversation_id)?.save_failure ?? null) !== null)
      return new PlannerError(
        'conversation_save_failed',
        'This conversation could not save, so it refuses new work until a restart.',
      );
    return this.options.execution_refusal(conversation_id);
  }
  native_operation<T>(operation: () => Promise<T>): Promise<T> {
    return system_shutdown.native_operation(this, operation);
  }
  note_written(state: ConversationState, rows: readonly StoredConversationEvent[]): void {
    if (rows.length === 0) return;
    let values = { ...state.record, latest_sequence: rows[rows.length - 1].sequence };
    for (const row of rows) {
      if (row.payload instanceof ModelChangedEventPayload) {
        values = {
          ...values,
          model: row.payload.model,
          reasoning_effort: row.payload.reasoning_effort,
        };
      }
    }
    state.record = new ConversationRecord(values);
    if (this.shutting_down) return;
    // A subscriber failure occurs after commit. Never put it inside a save retry.
    for (const row of rows) {
      try {
        this.options.publish_event?.(row);
      } catch (error) {
        console.error(
          `conversation ${state.record.conversation_id} could not publish committed event ${row.sequence}`,
          error,
        );
      }
    }
  }
  publish_held_prompts_changed(state: ConversationState): void {
    return send.publish_held_prompts_changed(this, state);
  }
  send(
    conversation_id: string,
    content: MessageContent,
    options: SendOptions,
  ): Promise<PromptDeliveryFate> {
    return send.send(this, conversation_id, content, options);
  }
  queue(state: ConversationState, message: Message): Promise<PromptDeliveryFate> {
    return send.queue(this, state, message);
  }
  hold_prompt(
    state: ConversationState,
    message: Message,
    position = state.held_prompts.length,
  ): PromptDeliveryQueued {
    return send.hold_prompt(this, state, message, position);
  }
  reserve_turn(
    state: ConversationState,
    automatic_compaction = false,
    sender_message_ids: readonly (string | null)[] = [],
  ): Promise<ReservedTurn> {
    return delivery.reserve_turn(this, state, automatic_compaction, sender_message_ids);
  }
  save_gave_up(state: ConversationState): boolean {
    return delivery.save_gave_up(this, state);
  }
  deliver_and_finalize(
    state: ConversationState,
    reservation: ReservedTurn,
    batch: readonly Message[],
    queued: boolean,
    drain_after_delivery_exception = true,
  ): Promise<PromptDeliveryFate> {
    return delivery.deliver_and_finalize(
      this,
      state,
      reservation,
      batch,
      queued,
      drain_after_delivery_exception,
    );
  }
  abandon_reservation(
    state: ConversationState,
    reservation: ReservedTurn,
    phase: 'idle' | 'draining',
  ): void {
    return delivery.abandon_reservation(this, state, reservation, phase);
  }
  finish_held_deliveries(state: ConversationState, batch: readonly Message[]): void {
    return send.finish_held_deliveries(this, state, batch);
  }
  mark_eligible_held(state: ConversationState): void {
    return send.mark_eligible_held(this, state);
  }
  defer_start_drain(state: ConversationState): void {
    return send.defer_start_drain(this, state);
  }
  schedule_drain(state: ConversationState): void {
    return send.schedule_drain(this, state);
  }
  drain_held_prompts(state: ConversationState): Promise<void> {
    return send.drain_held_prompts(this, state);
  }
  make_sink(state: ConversationState): CoreBackendEventSink {
    return backend_events.make_sink(this, state);
  }
  _confirm_local_child_stopped(state: ConversationState, child: BackendChild): Promise<void> {
    return child_lifecycle._confirm_local_child_stopped(this, state, child);
  }
  wait_for_reservation(
    state: ConversationState,
    token: TurnToken | null,
    retry_later_at?: number,
  ): Promise<void> {
    return delivery.wait_for_reservation(this, state, token, retry_later_at);
  }
  async take_local_backend_event(
    state: ConversationState,
    token: TurnToken | null,
    event: BackendEventValue,
    expected_sink?: BackendEventSink,
  ): Promise<boolean> {
    return take_backend_event(this, state, token, event, expected_sink);
  }
  take_backend_event(
    turn: WireValue<'TurnToken'> | null,
    event: BackendEventValue,
    options: {
      in_commit?: RecordCommitParticipant<WireValue<'JsonObject'>>;
      conversation_id?: string;
      retry_later_at?: number;
      expected_sink?: BackendEventSink;
    } = {},
  ): Promise<AcceptedRecordEvent<WireValue<'JsonObject'>>> {
    return remote.take_wire_backend_event(this, turn, event, options);
  }
  take_answer_application(
    turn: WireValue<'TurnToken'>,
    answer_id: string,
    provider_id: string,
    kind: string,
    in_commit: RecordCommitParticipant<WireValue<'JsonObject'>>,
    options: { retry_later_at?: number } = {},
  ): Promise<AcceptedRecordEvent<WireValue<'JsonObject'>>> {
    return remote.take_answer_application(
      this,
      admit_remote_turn(turn),
      answer_id,
      provider_id,
      kind,
      in_commit,
      options.retry_later_at,
    );
  }
  retained_run(state: ConversationState) {
    return remote.retained_run(this, state);
  }
  confirm_remote_prompt_accepted(
    reference: ChildReference,
    turn: WireValue<'TurnToken'>,
  ): Promise<void> {
    return remote.confirm_remote_prompt_accepted(this, reference, admit_remote_turn(turn));
  }
  reconcile_remote_command(
    command: WireValue<'ChildCommand'>,
    outcome: WireValue<'CommandOutcome'>,
  ): Promise<void> {
    return remote.reconcile_remote_command(this, command, outcome);
  }
  fence_replaced_remote_child(
    reference: ChildReference,
    options: { retry_later_at?: number } = {},
  ): Promise<void> {
    return remote.fence_replaced_remote_child(this, reference, options.retry_later_at);
  }
  confirm_remote_child_termination(reference: ChildReference): Promise<void> {
    return remote.confirm_remote_child_termination(this, reference);
  }
  retire_replaced_remote_child(
    reference: ChildReference,
    options: { replaced_by: string; undelivered_prompt?: UndeliveredTurnPrompt | null },
  ): Promise<void> {
    return remote.retire_replaced_remote_child(
      this,
      reference,
      options.replaced_by,
      options.undelivered_prompt,
    );
  }
  resume_undelivered_prompt(
    reference: ChildReference,
    prompt: UndeliveredTurnPrompt,
    options: { start_needed: boolean },
  ): Promise<void> {
    return remote.resume_undelivered_prompt(this, reference, prompt, options.start_needed);
  }
  stop_interrupted_remote_child(reference: ChildReference, child: BackendChild): Promise<void> {
    return remote.stop_interrupted_remote_child(this, reference, child);
  }
  emit_answer(turn: TurnToken, emit: () => Promise<unknown>): Promise<void> {
    return answers.emit_answer(this, turn, emit);
  }
  await_late_steer(
    conversation_id: string,
    sequence: number,
    not_delivered: Promise<boolean>,
  ): Promise<void> {
    return controls.await_late_steer(this, conversation_id, sequence, not_delivered);
  }
  begin_attempt(
    state: ConversationState,
    token: TurnToken,
    operation: LifecycleFact['operation'],
    provider_identity: string | null = null,
    related: readonly LifecycleFact[] = [],
    mutations: readonly RuntimeMutation[] = [],
  ): Promise<LifecycleFact> {
    return turns.begin_attempt(
      this,
      state,
      token,
      operation,
      provider_identity,
      related,
      mutations,
    );
  }
  revoke_turn_requests(state: ConversationState, running: RunningTurn): Promise<void> {
    return turns.revoke_turn_requests(this, state, running);
  }
  end_turn(
    state: ConversationState,
    running: RunningTurn,
    ending: import('./events.ts').ConversationTurnEnding,
    error: string | null,
    cancel_fact: LifecycleFact | null = null,
  ): Promise<StoredConversationEvent | null> {
    return turns.end_turn(this, state, running, ending, error, cancel_fact);
  }
  acquire_after_steers(state: ConversationState): Promise<LockLease> {
    return acquire_after_steers(state);
  }
  acquire_settled(state: ConversationState): Promise<LockLease> {
    return acquire_settled(state);
  }
  discard_child(
    state: ConversationState,
    child: import('./backends/contracts.ts').BackendChild,
  ): Promise<boolean> {
    return child_lifecycle.discard_child(this, state, child);
  }
  retire_child_for_rebind(state: ConversationState, child: BackendChild): Promise<boolean> {
    return child_lifecycle.retire_child_for_rebind(this, state, child);
  }
  async start_idle_child_janitor(): Promise<void> {
    this.require_runtime_ownership();
    if (!this.idle_janitor) {
      this.idle_janitor = new IdleChildJanitor(this);
      this.idle_janitor.start();
    }
  }
  async sweep_idle_children(): Promise<void> {
    return import('./runtime/maintenance.ts').then((module) => module.sweep_idle_children(this));
  }
  async interrupt(conversation_id: string): Promise<void> {
    const state = this.conversation_state(conversation_id);
    if (state) await own_settlement(state, controls.interrupt(this, state));
  }
  async kill(conversation_id: string): Promise<void> {
    const state = this.conversation_state(conversation_id);
    if (state) {
      await this.ensure_local_custody(state);
      this.deferred_start_conversations.delete(conversation_id);
      await own_settlement(state, controls.kill(this, state));
    }
  }
  async kill_for_restart(
    conversation_id: string,
    ticket_id: string,
    finish: Parameters<ConversationSystem['kill_for_restart']>[2],
  ): Promise<void> {
    const state = this.conversation_state(conversation_id);
    if (!state) {
      await finish(null);
      return;
    }
    await this.ensure_local_custody(state);
    this.deferred_start_conversations.delete(conversation_id);
    await own_settlement(state, controls.kill(this, state, { ticket_id, finish }));
  }
  configure_worker_cleanup_recheck(
    callback: (source: ChildReference, ticket_id: string) => Promise<CleanupResult>,
  ): void {
    this.worker_cleanup_recheck = callback;
  }
  recheck_worker_cleanup(ticket_id: string): Promise<CleanupResult> {
    return child_lifecycle.recheck_worker_cleanup(this, ticket_id);
  }
  confirm_killed_source(
    state: ConversationState,
    restart?: { ticket_id: string; finish: Parameters<ConversationSystem['kill_for_restart']>[2] },
  ): Promise<void> {
    return child_lifecycle.confirm_killed_source(this, state, restart);
  }
  async promote_held_prompt(
    conversation_id: string,
    held_prompt_id: string,
    mode: HeldPromptPromotionMode,
  ): Promise<PromptDeliveryFate | null> {
    this.require_runtime_ownership();
    const refusal = this.execution_refusal(conversation_id);
    if (refusal !== null) throw refusal;
    const state = this.conversation_state(conversation_id);
    return state
      ? own_settlement(state, controls.promote_held_prompt(this, state, held_prompt_id, mode))
      : null;
  }
  async discard_held_prompt(
    conversation_id: string,
    held_prompt_id: string,
    withdrawn_for_edit = false,
  ): Promise<boolean> {
    const state = this.conversation_state(conversation_id);
    return state
      ? own_settlement(
          state,
          controls.discard_held_prompt(this, state, held_prompt_id, withdrawn_for_edit),
        )
      : false;
  }
  async answer_permission_ask(
    conversation_id: string,
    ask_id: string,
    option_id: string,
    operation_id: string | null = null,
  ): Promise<AnswerFate> {
    const state = this.conversation_state(conversation_id);
    return state
      ? own_settlement(
          state,
          answers.answer_permission_ask(this, state, ask_id, option_id, operation_id),
        )
      : AnswerFate.refused;
  }
  async answer_user_input(
    conversation_id: string,
    request_id: string,
    values: readonly import('./events.ts').UserInputAnswer[],
    operation_id: string | null = null,
  ): Promise<AnswerFate> {
    const state = this.conversation_state(conversation_id);
    return state
      ? own_settlement(
          state,
          answers.answer_user_input(this, state, request_id, values, operation_id),
        )
      : AnswerFate.refused;
  }
  /** The saved fate of an answer sent with this operation ID, or null if none was saved. */
  answer_receipt(conversation_id: string, operation_id: string): AnswerFate | null {
    return answers.answer_receipt(this, conversation_id, operation_id);
  }
  pending_permission_ask_ids(conversation_id: string): Promise<ReadonlySet<string>> {
    return queries.pending_permission_ask_ids(this, conversation_id);
  }
  pending_user_input_request_ids(conversation_id: string): Promise<ReadonlySet<string>> {
    return queries.pending_user_input_request_ids(this, conversation_id);
  }
  run_if_idle(conversation_id: string, operation: () => void): Promise<boolean> {
    return queries.run_if_idle(this, conversation_id, operation);
  }
  record_events_transaction<T>(
    conversation_id: string,
    operation: (
      tx: import('../core/database/contracts.ts').TransactionSql,
    ) => import('./record/contracts.ts').EventMutation<T>,
  ): Promise<T> {
    return send.record_events_transaction(this, conversation_id, operation);
  }
  held_prompts(conversation_id: string): Promise<readonly HeldPrompt[]> {
    return queries.held_prompts(this, conversation_id);
  }
  owner_send_progress(
    conversation_id: string,
    sender_message_id: string,
    recipient: Principal,
  ): Promise<OwnerSendProgress | null> {
    return queries.owner_send_progress(this, conversation_id, sender_message_id, recipient);
  }
  sender_message_ids_awaiting_outcome(conversation_id: string): Promise<ReadonlySet<string>> {
    return queries.sender_message_ids_awaiting_outcome(this, conversation_id);
  }
  is_running(conversation_id: string): Promise<boolean> {
    return queries.is_running(this, conversation_id);
  }
  supports_active_steering(
    backend_key: import('../drivers/core/identity.ts').BackendInstanceId,
  ): boolean {
    return queries.supports_active_steering(this, backend_key);
  }
  has_pending_permission_ask(conversation_id: string): Promise<boolean> {
    return queries.has_pending_permission_ask(this, conversation_id);
  }
  has_pending_user_input(conversation_id: string): Promise<boolean> {
    return queries.has_pending_user_input(this, conversation_id);
  }
  record_message_to_owner(
    conversation_id: string,
    content: MessageContent,
    options: RecordMessageToOwnerOptions,
  ): Promise<void> {
    return send.record_message_to_owner(this, conversation_id, content, options);
  }
  workspace_activity(): 'idle' | 'busy' | 'unknown' {
    return queries.workspace_activity(this);
  }
  update_interrupted_work(machine_ids?: readonly string[]): readonly string[] {
    return update_preparation.update_interrupted_work(this, machine_ids);
  }
  reopen_after_update(): void {
    update_preparation.reopen_after_update(this);
  }
  readonly retire_local_workspace = (
    ticket_id: string,
    identity: WorkspaceIdentity,
    operation: () => MaybePromise<RetirementTarget>,
  ): Promise<RetirementTarget> =>
    child_lifecycle.retire_local_workspace(this, ticket_id, identity, operation);
  run_with_workspace_admission(
    operation: () => void,
    options: { require_idle: boolean },
  ): Promise<boolean> {
    return update_preparation.run_with_workspace_admission(this, operation, options);
  }
  wait_until_quiescent(): Promise<void> {
    return system_shutdown.wait_until_quiescent(this);
  }
  settled(): Promise<void> {
    return system_shutdown.settled(this);
  }
  shutdown(options: import('../lifecycle/contracts.ts').StopOptions): Promise<void> {
    return system_shutdown.shutdown(this, options);
  }
}
