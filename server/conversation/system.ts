import { PlannerError } from '../core/contracts.ts';
import type {
  WorkspaceIdentity,
  RetirementTarget,
  MaybePromise,
} from '../ticket_workspaces/contracts.ts';
import type { CleanupResult } from '../worker_supervision/contracts.ts';
import { AutomaticStartAdmission } from './start_admission/index.ts';
import type { StartRequest } from './start_admission/contracts.ts';
import { is_shutdown_business_cancellation } from './shutdown.ts';
/** Local process core. Registered as ConversationSystem after the turn-control slice lands. */
import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { isDeepStrictEqual } from 'node:util';
import {
  AgentMessageDelta,
  BackendAdapterError,
  ModelThinkingHappened,
  PromptWriteFailed,
  ToolCallProgress,
} from './backends/contracts.ts';
import type { BackendEventSink, BackendEventValue, TurnToken } from './backends/contracts.ts';
import { log_backend_failure } from './backends/failure_summary.ts';
import type { BackendChild, ChildReference } from './backends/contracts.ts';
import type { WireValue } from '../executor/contracts.ts';
import {
  AnswerFate,
  HeldPrompt,
  PromptDeliveryMode,
  PromptDeliveryQueued,
  PromptDeliveryRefused,
  PromptDeliveryStarted,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
  PromptQueueReason,
} from './contracts.ts';
import type {
  ConversationStartRequest,
  ConversationSystem,
  HeldPromptPromotionMode,
  PromptDeliveryFate,
  RecordMessageToOwnerOptions,
  SendOptions,
} from './contracts.ts';
import {
  AgentMessageDeltaFrame,
  ModelChangedEventPayload,
  ModelThinkingFrame,
  ToolCallProgressFrame,
  PromptEventPayload,
  PromptDiscardedEventPayload,
  PromptDeliveryRefusedEventPayload,
  PromptDeliveryUncertainEventPayload,
} from './events.ts';
import type { ConversationLiveTailFrame } from './events.ts';
import type { MessageContent } from './message_content.ts';
import { ConversationRecord } from './record/contracts.ts';
import type {
  RunningTurn,
  StoredConversationEvent,
  RecordCommitParticipant,
  AcceptedRecordEvent,
  UndeliveredTurnPrompt,
} from './record/contracts.ts';
import {
  LifecycleKind as K,
  LifecycleOperation as O,
  LifecycleStatus as S,
} from './record/lifecycle.ts';
import type { LifecycleFact } from './record/lifecycle.ts';
import type {
  CoreBackendEventSink,
  ConversationCoreOptions,
  DeliveryAttempt,
  DeliveryServices,
  Message,
  ReservedTurn,
  RuntimeMutation,
} from './runtime/contracts.ts';
import { deliver_prompt } from './runtime/delivery.ts';
import { MODEL_THINKING_PULSE_INTERVAL_SECONDS } from './runtime/configuration.ts';
import { take_backend_event, take_backend_event_with_receipt } from './runtime/backend_events.ts';
import { wait_for_item } from '../executor/item_budget.ts';
import * as remote from './runtime/remote.ts';
import { admit_remote_turn } from './runtime/remote_turn.ts';
import * as controls from './runtime/controls.ts';
import * as answers from './runtime/answers.ts';
import * as start from './runtime/start.ts';
import * as send from './runtime/send.ts';
import * as child_lifecycle from './runtime/child_lifecycle.ts';
import * as turns from './runtime/turns.ts';
import * as queries from './runtime/queries.ts';
import * as update_preparation from './runtime/update_preparation.ts';
import * as system_shutdown from './runtime/system_shutdown.ts';
import { acquire_after_steers, acquire_settled } from './runtime/synchronization.ts';
import type { LockLease } from './runtime/synchronization.ts';
import {
  IdleChildJanitor,
  LostBackgroundTaskNotices,
  tell_agent_about_lost_background_tasks,
} from './runtime/maintenance.ts';
import { prompt_row, refused_row, uncertain_row } from './runtime/messages.ts';
import { field_mutation, save_attempt } from './runtime/save_attempt.ts';
import { Deferred } from '../core/deferred.ts';
import { ConversationState, own_settlement, same_turn, set_phase } from './runtime/state.ts';
import { WorkspaceAdmission } from './workspace_admission.ts';

function turn_fact(token: TurnToken, status: LifecycleFact['status']): LifecycleFact {
  return {
    kind: K.turn,
    identity: String(token.turn_number),
    turn_number: token.turn_number,
    operation: O.turn,
    status,
    provider_identity: null,
  };
}
function prompt_fact(token: TurnToken, status: LifecycleFact['status']): LifecycleFact {
  return {
    kind: K.attempt,
    identity: `prompt:${token.turn_number}`,
    turn_number: token.turn_number,
    operation: O.prompt,
    status,
    provider_identity: null,
  };
}
function new_running_turn(reservation: ReservedTurn): RunningTurn {
  return {
    token: reservation.token,
    automatic_compaction: reservation.automatic_compaction,
    automatic_notice: reservation.automatic_notice,
    memory_notice_only: reservation.memory_notice_only,
    compaction_confirmed: false,
    pending_permission_ask_ids: new Set(),
    permission_provider_ids: new Map(),
    user_input_provider_ids: new Map(),
    pending_user_input_questions: new Map(),
    ended: false,
    callbacks_revoked: false,
    ending_is_the_cores: false,
  };
}

function save_failure_detail(error: unknown): string {
  const code =
    error !== null && typeof error === 'object' && 'code' in error && typeof error.code === 'string'
      ? error.code
      : null;
  return code !== null ? `Save error ${code}` : `Save error: ${String(error)}`;
}
export class SqliteProcessConversationSystem implements DeliveryServices, ConversationSystem {
  readonly options: ConversationCoreOptions;
  readonly workspace_admission = new WorkspaceAdmission();
  readonly start_admission: AutomaticStartAdmission;
  readonly retiring_workspace_tickets = new Set<string>();
  readonly retiring_workspace_conversations = new Set<string>();
  readonly local_child_generations = new WeakMap<BackendChild, string>();
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
  update_generation: symbol | null = null;
  update_admission_owned = false;
  update_metadata: ReadonlyMap<string, readonly [string, string]> | null = null;
  update_retirement: Promise<void> | null = null;
  update_retirement_generation: symbol | null = null;
  update_retirement_pending = false;
  update_stop_failed = false;
  acknowledged_event_failures = new Map<string, number>();
  idle_janitor: IdleChildJanitor | null = null;
  readonly lost_task_notices: LostBackgroundTaskNotices;
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
    this.lost_task_notices = new LostBackgroundTaskNotices(this);
    this.start_admission = new AutomaticStartAdmission({ clock: () => this.monotonic_now() });
    const adopted = remote.initialize_retained_runs(this);
    this.background_tasks_left_by_previous_process = new Map(
      [...options.store.read_live_background_task_ids()].filter(([id]) => !adopted.has(id)),
    );
  }
  async tell_agents_of_background_tasks_lost_at_restart(): Promise<void> {
    this.require_runtime_ownership();
    const left_behind = this.background_tasks_left_by_previous_process;
    this.background_tasks_left_by_previous_process = new Map();
    for (const [conversation_id, task_ids] of [...left_behind].sort(([first], [second]) =>
      first < second ? -1 : first > second ? 1 : 0,
    )) {
      try {
        const state = this.conversation_state(conversation_id);
        if (state === null) continue;
        const lost = await this.persist(state, () =>
          this.options.store.take_background_tasks(conversation_id, { task_ids }),
        );
        tell_agent_about_lost_background_tasks(this, state, lost);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') throw error;
        console.error(
          `conversation ${conversation_id} could not hear about background tasks lost at restart`,
          error,
        );
      }
    }
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
  async reserve_turn(
    state: ConversationState,
    automatic_compaction = false,
    sender_message_ids: readonly (string | null)[] = [],
  ): Promise<ReservedTurn> {
    const requests = this.start_admission.requests_for(sender_message_ids);
    let number: number;
    try {
      number = await this.persist(state, () =>
        this.options.store.reserve_turn(state.record.conversation_id, requests),
      );
    } catch (error) {
      // A cancel just before this left the phase ending. A save that gave up must not keep it there.
      if (state.phase === 'ending')
        set_phase(state, state.running_turn === null ? 'idle' : 'running');
      throw error;
    }
    this.start_admission.bind(sender_message_ids, {
      conversation_id: state.record.conversation_id,
      turn_number: number,
    });
    const reserved: ReservedTurn = {
      token: { conversation_id: state.record.conversation_id, turn_number: number },
      resolved: new Deferred<void>(),
      automatic_compaction,
      automatic_notice: false,
      memory_notice_only: false,
      prompt_accepted: false,
      prompt_write_attempted: false,
      messages: [],
      mode: PromptDeliveryMode.queue,
      shutdown_owned: false,
      shutdown_fate: null,
    };
    state.model_thinking_shown_at_monotonic = 0;
    state.reserved_turn = reserved;
    set_phase(state, 'starting');
    this.mark_eligible_held(state);
    return reserved;
  }
  /** True when a save of this conversation gave up, or the database is unusable. */
  save_gave_up(state: ConversationState): boolean {
    return state.save_failure !== null || this.options.store.database_unusable_error !== null;
  }
  async deliver_and_finalize(
    state: ConversationState,
    reservation: ReservedTurn,
    batch: readonly Message[],
    queued: boolean,
    drain_after_delivery_exception = true,
  ): Promise<PromptDeliveryFate> {
    try {
      return await this.deliver_and_record(
        state,
        reservation,
        batch,
        queued,
        drain_after_delivery_exception,
      );
    } catch (error) {
      if (
        (error instanceof Error && error.name === 'AbortError') ||
        this.shutting_down ||
        !this.save_gave_up(state)
      )
        throw error;
      // A save gave up partway through this delivery. Release the reservation, so the
      // conversation settles and a stop does not wait for it.
      await state.lock.run(() => {
        // Release only this delivery's own reservation, never a newer one.
        if (
          state.reserved_turn === reservation ||
          (state.reserved_turn === null && !reservation.resolved.settled)
        )
          this.abandon_reservation(state, reservation, 'idle');
        this.finish_held_deliveries(state, batch);
      });
      // The agent can have this prompt, so the sender must not hear that it was not sent.
      if (reservation.prompt_write_attempted) return new PromptDeliveryUncertain();
      throw error;
    }
  }
  private async deliver_and_record(
    state: ConversationState,
    reservation: ReservedTurn,
    batch: readonly Message[],
    queued: boolean,
    drain_after_delivery_exception: boolean,
  ): Promise<PromptDeliveryFate> {
    reservation.messages = batch;
    this.start_admission.bind(
      batch.map((message) => message.sender_message_id),
      reservation.token,
    );
    const promoted = queued && reservation.mode === PromptDeliveryMode.send_now;
    let delivery: DeliveryAttempt;
    try {
      await this.persist(state, () =>
        this.start_admission.persist_bindings(
          this.options.store.start_admission_database,
          reservation.token,
        ),
      );
      reservation.automatic_notice = await this.persist(state, () =>
        this.options.store.all_sender_messages_are_manager_notices(
          state.record.conversation_id,
          batch.map((message) => message.sender_message_id),
        ),
      );
      delivery = await deliver_prompt(this, state, reservation, batch);
    } catch (error) {
      if (reservation.shutdown_owned)
        return reservation.shutdown_fate ?? new PromptDeliveryUncertain();
      if (this.shutting_down) throw error;
      await this.persist(state, () =>
        this.start_admission.failed_attempt(
          this.options.store.start_admission_database,
          reservation.token,
        ),
      );
      const attempted = reservation.prompt_write_attempted ? reservation.messages : batch;
      // Python records ordinary post-write errors as possible emission, while
      // business cancellation keeps its existing path.
      if (
        error instanceof PromptWriteFailed ||
        (reservation.prompt_write_attempted && !is_shutdown_business_cancellation(error))
      ) {
        // An uncertain delivery row has no text, so the log keeps the backend's.
        if (error instanceof BackendAdapterError)
          log_backend_failure(state.record.conversation_id, state.record.backend_key, error);
        if (queued && !reservation.prompt_write_attempted)
          await this.settle_automatic_ids(
            state,
            batch.map((message) => message.sender_message_id),
          );
        await this.record_delivery_uncertainty(state, reservation, attempted, batch);
        return new PromptDeliveryUncertain();
      }
      const recovery_messages = batch.filter((message) => message.recovery_references.length > 0);
      if (recovery_messages.length === 0) {
        await this.persist(state, () =>
          this.options.store.fail_recovery_claims(
            state.record.conversation_id,
            reservation.token.turn_number,
          ),
        );
      } else {
        await state.lock.run(async () => {
          for (const [index, message] of recovery_messages.entries()) {
            const rows = await this.persist(state, () =>
              this.options.store.append_events(
                state.record.conversation_id,
                [uncertain_row(message, reservation.mode)],
                {
                  lifecycle:
                    index === 0
                      ? [
                          turn_fact(reservation.token, S.unavailable),
                          prompt_fact(reservation.token, S.unknown),
                        ]
                      : [],
                  recording_receipt: message.recording_receipt,
                },
              ),
            );
            this.note_written(state, rows);
          }
        });
      }
      await state.lock.run(() => {
        if (queued) {
          state.child_is_quarantined = true;
          for (const message of [...attempted].reverse())
            if (!message.dispatch_memory_note)
              state.held_prompts.unshift({ ...message, delivery_uncertain: true });
          this.finish_held_deliveries(state, batch);
        }
        this.abandon_reservation(state, reservation, 'idle');
      });
      if (!queued || promoted) {
        if (drain_after_delivery_exception) this.schedule_drain(state);
        throw error;
      }
      return new PromptDeliveryUncertain();
    }
    if (delivery.suppression_reason !== null) {
      await this.settle_automatic_ids(
        state,
        batch.map((message) => message.sender_message_id),
      );
      await state.lock.run(() => {
        this.abandon_reservation(state, reservation, queued && !promoted ? 'draining' : 'idle');
        this.finish_held_deliveries(state, batch);
      });
      if (!queued || promoted) this.schedule_drain(state);
      return new PromptDeliverySuppressed({ reason: delivery.suppression_reason });
    }
    reservation.automatic_notice = delivery.automatic_notice;
    reservation.memory_notice_only = delivery.memory_notice_only;
    const survivors = delivery.messages.length > 0 ? delivery.messages : batch;
    // A drain credits only the successful admission envelopes returned by delivery.
    // A definite refusal returns none. The remaining refused rows never carry read credit.
    const drain_bounds = delivery.messages
      .filter(
        (message) =>
          message.sender?.kind === 'owner' && message.owner_read_through_sequence !== null,
      )
      .map((message) => message.owner_read_through_sequence!);
    const drain_read_through = drain_bounds.length > 0 ? Math.max(...drain_bounds) : null;
    let receipt_save_gave_up = false;
    const fate = await state.lock.run(async () => {
      if (reservation.shutdown_owned)
        return reservation.shutdown_fate ?? new PromptDeliveryUncertain();
      try {
        if (delivery.refusal_reason !== null) {
          const lifecycle = [
            turn_fact(reservation.token, S.unavailable),
            prompt_fact(reservation.token, S.refused),
          ];
          if (delivery.record_refusal || queued) {
            for (let index = 0; index < survivors.length; index += 1) {
              const message = survivors[index];
              const rows = await this.persist(state, () =>
                this.options.store.append_events(
                  state.record.conversation_id,
                  [
                    refused_row(
                      message,
                      reservation.mode,
                      delivery.refusal_reason!,
                      delivery.refusal_detail,
                    ),
                  ],
                  {
                    lifecycle: index === 0 ? lifecycle : [],
                    recording_receipt: message.recording_receipt,
                    owner_read_through_sequence:
                      index !== 0
                        ? null
                        : queued && !promoted
                          ? drain_read_through
                          : message.acknowledge_owner_on_failure
                            ? message.owner_read_through_sequence
                            : null,
                  },
                ),
              );
              this.note_written(state, rows);
            }
          } else {
            await this.persist(state, () =>
              this.options.store.transition_lifecycle(state.record.conversation_id, lifecycle),
            );
          }
          await this.settle_automatic_ids(
            state,
            survivors.map((message) => message.sender_message_id),
          );
          set_phase(state, queued && !promoted ? 'draining' : 'idle');
          return new PromptDeliveryRefused({ refusal_reason: delivery.refusal_reason });
        }
        const first = survivors[0];
        const effort_is_set = survivors.some((message) => message.reasoning_effort_is_set);
        const change =
          first.model_change !== null || first.reasoning_effort_change !== null || effort_is_set
            ? new ModelChangedEventPayload({
                model: first.model_change ?? state.record.model!,
                reasoning_effort:
                  first.reasoning_effort_change === null && !effort_is_set
                    ? state.record.reasoning_effort
                    : first.reasoning_effort_change,
              })
            : null;
        const owner_bounds = survivors
          .filter(
            (message) =>
              message.sender?.kind === 'owner' && message.owner_read_through_sequence !== null,
          )
          .map((message) => message.owner_read_through_sequence!);
        const boundary = queued
          ? owner_bounds.length
            ? Math.max(...owner_bounds)
            : null
          : first.owner_read_through_sequence;
        const running = new_running_turn(reservation);
        let written: readonly StoredConversationEvent[];
        try {
          written = await this.persist(
            state,
            () =>
              this.options.store.append_delivered_prompt(state.record.conversation_id, {
                prompt: prompt_row(first, reservation.mode),
                extra_prompts: survivors
                  .slice(1)
                  .map((message) => prompt_row(message, reservation.mode)),
                model_change: change,
                lifecycle: [
                  turn_fact(reservation.token, S.running),
                  prompt_fact(reservation.token, S.started),
                ],
                composed_content_delivered: delivery.composed_content_delivered,
                recording_receipt: first.recording_receipt,
                extra_recording_receipts: survivors
                  .slice(1)
                  .flatMap((message) =>
                    message.recording_receipt ? [message.recording_receipt] : [],
                  ),
                owner_read_through_sequence: boundary,
              }),
            [field_mutation(state, 'running_turn', running)],
          );
        } catch (error) {
          if (error instanceof Error && error.name === 'AbortError') throw error;
          receipt_save_gave_up = true;
          return new PromptDeliveryUncertain();
        }
        this.note_written(state, written);
        const owner_rows = written.filter(
          (row) => row.payload.kind === 'prompt' && row.payload.sender?.kind === 'owner',
        );
        if (owner_rows.length > 0)
          state.record = new ConversationRecord({
            ...state.record,
            owner_read_through_sequence: Math.max(
              state.record.owner_read_through_sequence,
              boundary ?? Math.max(...owner_rows.map((row) => row.sequence)),
            ),
          });
        state.has_delivered_prompt = true;
        state.delivery_uncertain = false;
        set_phase(state, 'running');
        this.finish_held_deliveries(state, reservation.messages);
        if (!this.shutting_down) {
          this.options.emit_change?.();
          if (change && queued) {
            await this.options.on_held_model_change?.(
              state.record.conversation_id,
              change.model,
              change.reasoning_effort,
            );
          }
        }
        return new PromptDeliveryStarted();
      } finally {
        if (state.phase !== 'starting') {
          state.reserved_turn = null;
          reservation.resolved.resolve();
        }
      }
    });
    if (receipt_save_gave_up) {
      // The agent has this prompt, but its receipt save gave up. Record it as possibly
      // delivered, so no sender resends it.
      await this.record_delivery_uncertainty(state, reservation, survivors, batch).catch(
        (failure: unknown) =>
          console.error(
            `conversation ${state.record.conversation_id} could not record an uncertain delivery`,
            failure,
          ),
      );
      await state.lock.run(() => this.abandon_reservation(state, reservation, 'idle'));
    }
    await state.lock.run(() => this.finish_held_deliveries(state, batch));
    if (fate instanceof PromptDeliveryRefused && (!queued || promoted)) this.schedule_drain(state);
    return fate;
  }
  abandon_reservation(
    state: ConversationState,
    reservation: ReservedTurn,
    phase: 'idle' | 'draining',
  ): void {
    if (state.reserved_turn === reservation) state.reserved_turn = null;
    set_phase(state, phase);
    reservation.resolved.resolve();
  }
  private async record_delivery_uncertainty(
    state: ConversationState,
    reservation: ReservedTurn,
    batch: readonly Message[],
    settle_messages: readonly Message[] = batch,
  ): Promise<void> {
    await state.lock.run(async () => {
      for (const message of batch)
        if (message.sender_message_id !== null)
          this.start_admission.uncertain(message.sender_message_id);
      state.child_is_quarantined = true;
      state.delivery_uncertain = true;
      for (const message of batch) {
        const rows = await this.persist(state, () =>
          this.options.store.append_events(
            state.record.conversation_id,
            [uncertain_row(message, reservation.mode)],
            {
              lifecycle: [prompt_fact(reservation.token, S.unknown)],
              recording_receipt: message.recording_receipt,
            },
          ),
        );
        this.note_written(state, rows);
      }
      this.finish_held_deliveries(state, settle_messages);
      const present = new Set(state.held_prompts.map((message) => message.held_prompt_id));
      for (const message of [...batch].reverse()) {
        if (message.dispatch_memory_note) continue;
        if (message.held_prompt_id && !present.has(message.held_prompt_id))
          state.held_prompts.unshift({ ...message, delivery_uncertain: true });
      }
      set_phase(state, 'idle');
      this.mark_eligible_held(state);
      this.publish_held_prompts_changed(state);
    });
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
    const generation = randomUUID().replaceAll('-', '');
    const sink: CoreBackendEventSink = {
      generation,
      record_local_child_supervision: (path) =>
        this.options.store.record_child_supervision(state.record.conversation_id, generation, path),
      record_local_child_custody: (identity) =>
        this.options.store.record_child_custody(state.record.conversation_id, generation, identity),
      report: (token, event) =>
        own_settlement(
          state,
          (async () => {
            if (this.shutting_down || state.child_event_sink !== sink) return;
            if (
              token !== null &&
              same_turn(state.running_turn?.token ?? null, token) &&
              !state.running_turn?.ended
            )
              await this.accept_automatic_start(state, token);
            if (
              event instanceof AgentMessageDelta ||
              event instanceof ModelThinkingHappened ||
              event instanceof ToolCallProgress
            ) {
              if (token === null) return;
              const show = async () => {
                await this.wait_for_reservation(state, token);
                if (
                  state.child_event_sink === sink &&
                  same_turn(state.running_turn?.token ?? null, token) &&
                  !state.running_turn?.ended
                ) {
                  await this.accept_automatic_start(state, token);
                  this.publish_live(state, token, event);
                }
              };
              if (state.reserved_turn) this.enqueue_backend_event(state, show);
              else this.publish_live(state, token, event);
              return;
            }
            this.enqueue_backend_event(state, async () => {
              await this.take_local_backend_event(state, token, event, sink);
            });
          })().catch((error: unknown) => {
            state.backend_event_failures += 1;
            throw error;
          }),
        ),
    };
    return sink;
  }
  record_local_child_preparation(
    state: ConversationState,
    child: BackendChild,
    sink: CoreBackendEventSink,
  ): Promise<void> {
    return child_lifecycle.record_local_child_preparation(this, state, child, sink);
  }
  check_local_child_started(state: ConversationState, turn: TurnToken): Promise<boolean> {
    return child_lifecycle.check_local_child_started(this, state, turn);
  }
  _confirm_local_child_stopped(state: ConversationState, child: BackendChild): Promise<void> {
    return child_lifecycle._confirm_local_child_stopped(this, state, child);
  }
  private publish_live(
    state: ConversationState,
    token: TurnToken,
    event: AgentMessageDelta | ModelThinkingHappened | ToolCallProgress,
  ): void {
    if (this.shutting_down || !same_turn(state.running_turn?.token ?? null, token)) return;
    this.options.require_runtime_ownership?.();
    let frame: ConversationLiveTailFrame;
    if (event instanceof AgentMessageDelta) frame = new AgentMessageDeltaFrame(event);
    else if (event instanceof ToolCallProgress) frame = new ToolCallProgressFrame(event);
    else {
      const now = this.monotonic_now();
      if (now - state.model_thinking_shown_at_monotonic < MODEL_THINKING_PULSE_INTERVAL_SECONDS)
        return;
      state.model_thinking_shown_at_monotonic = now;
      frame = new ModelThinkingFrame();
    }
    this.options.publish_frame?.(state.record.conversation_id, frame);
  }
  private enqueue_backend_event(state: ConversationState, event: () => Promise<void>): void {
    state.backend_events.push(event);
    if (state.backend_event_pump !== null || this.shutting_down) return;
    const pump = async () => {
      try {
        while (state.backend_events.length > 0 && !this.shutting_down) {
          const take = state.backend_events.shift()!;
          try {
            await take();
          } catch (error) {
            state.backend_event_failures += 1;
            console.error(
              `conversation ${state.record.conversation_id} could not take in a backend event`,
              error,
            );
          }
        }
      } finally {
        state.backend_event_pump = null;
      }
    };
    // Start after assignment, so a synchronous event cannot clear then overwrite the pump.
    state.backend_event_pump = Promise.resolve().then(pump);
  }
  async wait_for_reservation(
    state: ConversationState,
    token: TurnToken | null,
    retry_later_at?: number,
  ): Promise<void> {
    while (
      state.reserved_turn &&
      !state.reserved_turn.resolved.settled &&
      same_turn(state.reserved_turn.token, token)
    ) {
      if (this.shutting_down) return;
      await wait_for_item(state.reserved_turn.resolved.promise, retry_later_at);
    }
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
    this.require_runtime_ownership();
    const token = turn === null ? null : admit_remote_turn(turn);
    if (
      token !== null &&
      options.conversation_id !== undefined &&
      options.conversation_id !== token.conversation_id
    )
      throw new TypeError('event conversation conflicts with turn');
    const id = token?.conversation_id ?? options.conversation_id;
    if (id === undefined) throw new TypeError('a session event must name its conversation');
    const state = this.conversation_state(id);
    if (state === null) throw new TypeError('event names an unknown conversation');
    return own_settlement(
      state,
      take_backend_event_with_receipt(this, state, token, event, options),
    );
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
  async emit_answer(turn: TurnToken, emit: () => Promise<unknown>): Promise<void> {
    const state = this.conversation_state(turn.conversation_id);
    if (state !== null)
      await state.lock.run(async () => {
        if (
          same_turn(state.running_turn?.token ?? null, turn) &&
          !state.running_turn!.callbacks_revoked
        )
          await emit();
      });
  }
  async await_late_steer(
    conversation_id: string,
    sequence: number,
    not_delivered: Promise<boolean>,
  ): Promise<void> {
    this.require_runtime_ownership();
    const state = this.conversation_state(conversation_id);
    if (state === null) return;
    const row = this.options.store.read_event(conversation_id, sequence);
    if (
      !(row?.payload instanceof PromptDeliveryUncertainEventPayload) ||
      row.payload.mode !== PromptDeliveryMode.steer
    )
      return;
    const payload = row.payload;
    const later = this.options.store.read_events_after(conversation_id, sequence);
    if (
      later.some(
        (event) =>
          (event.payload instanceof PromptEventPayload ||
            event.payload instanceof PromptDeliveryRefusedEventPayload ||
            event.payload instanceof PromptDeliveryUncertainEventPayload ||
            event.payload instanceof PromptDiscardedEventPayload) &&
          (payload.sender_message_id !== null
            ? event.payload.sender_message_id === payload.sender_message_id
            : event.payload instanceof PromptEventPayload &&
              isDeepStrictEqual(event.payload.content, payload.content) &&
              event.payload.sender_label === payload.sender_label),
      )
    )
      return;
    controls.watch_late_steer(
      this,
      state,
      not_delivered,
      {
        held_prompt_id: '',
        content: payload.content,
        sender_label: payload.sender_label,
        model_change: null,
        reasoning_effort_change: null,
        reasoning_effort_is_set: false,
        sender_message_id: payload.sender_message_id,
        sent_at_unix_milliseconds: payload.sent_at_unix_milliseconds,
        snapshot_sent_at_unix_milliseconds: 0,
        sender: payload.sender,
        recipient: payload.recipient,
        queue_reason: PromptQueueReason.steer_refused,
        owner_read_through_sequence: null,
        acknowledge_owner_on_failure: false,
        recovery_references: payload.recovery_references,
        recording_receipt: null,
        delivery_uncertain: false,
        system_generated: payload.sender === null,
      },
      [],
    );
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
  async discard_held_prompt(conversation_id: string, held_prompt_id: string): Promise<boolean> {
    const state = this.conversation_state(conversation_id);
    return state
      ? own_settlement(state, controls.discard_held_prompt(this, state, held_prompt_id))
      : false;
  }
  async answer_permission_ask(
    conversation_id: string,
    ask_id: string,
    option_id: string,
  ): Promise<AnswerFate> {
    const state = this.conversation_state(conversation_id);
    return state
      ? own_settlement(state, answers.answer_permission_ask(this, state, ask_id, option_id))
      : AnswerFate.refused;
  }
  async answer_user_input(
    conversation_id: string,
    request_id: string,
    values: readonly import('./events.ts').UserInputAnswer[],
  ): Promise<AnswerFate> {
    const state = this.conversation_state(conversation_id);
    return state
      ? own_settlement(state, answers.answer_user_input(this, state, request_id, values))
      : AnswerFate.refused;
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
  sender_message_ids_awaiting_outcome(conversation_id: string): Promise<ReadonlySet<string>> {
    return queries.sender_message_ids_awaiting_outcome(this, conversation_id);
  }
  is_running(conversation_id: string): Promise<boolean> {
    return queries.is_running(this, conversation_id);
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
  update_waiting_work(): readonly string[] {
    return update_preparation.update_waiting_work(this);
  }
  prepare_update(mode: 'idle' | 'now', signal?: AbortSignal): Promise<boolean> {
    return update_preparation.prepare_update(this, mode, signal);
  }
  cancel_update(): boolean {
    return update_preparation.cancel_update(this);
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
