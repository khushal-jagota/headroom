import { conversation_link_lock } from '../runtime/conversation_start.ts';
import { PlannerError } from '../core/contracts.ts';
import type {
  WorkspaceIdentity,
  RetirementTarget,
  MaybePromise,
} from '../ticket_workspaces/contracts.ts';
import type { CleanupResult } from '../worker_supervision/contracts.ts';
import { cleanup_result } from '../worker_supervision/inspection.ts';
import { decode_json } from '../executor/protocol.ts';
import { PROTOCOL_VERSION } from '../execution_configuration.ts';
import { LEASE_CONTINUITY_SECONDS } from '../executor/configuration.ts';
import { AutomaticStartAdmission } from './start_admission/index.ts';
import type { StartRequest } from './start_admission/contracts.ts';
import { turn_is_maintenance } from './runtime/state.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import { observe_promise } from '../core/promise_observer.ts';
import { is_shutdown_business_cancellation } from './shutdown.ts';
/** Local process core. Registered as ConversationSystem after the turn-control slice lands. */
import { randomUUID } from 'node:crypto';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { performance } from 'node:perf_hooks';
import { isDeepStrictEqual } from 'node:util';
import {
  AgentMessageCompleted,
  AgentMessageDelta,
  ComposerCatalogReported,
  ContextCompacted,
  ModelThinkingHappened,
  PermissionAskRaised,
  PromptWriteFailed,
  ToolCallProgress,
  TurnEnded,
  UserInputRequested,
  VendorSessionCursorRebound,
} from './backends/contracts.ts';
import type { BackendEventSink, BackendEventValue, TurnToken } from './backends/contracts.ts';
import type { BackendChild, ChildReference } from './backends/contracts.ts';
import type { WireValue } from '../executor/contracts.ts';
import {
  ConversationBackendKey,
  AnswerFate,
  HeldPrompt,
  PromptDeliveryInjected,
  PromptDeliveryMode,
  PromptDeliveryQueued,
  PromptDeliveryRefused,
  PromptDeliveryRefusalReason as R,
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
  HeldPromptsChangedFrame,
  MessageToOwnerEventPayload,
  ModelChangedEventPayload,
  ModelThinkingFrame,
  ToolCallProgressFrame,
  PromptEventPayload,
  PromptDiscardedEventPayload,
  PromptDeliveryRefusedEventPayload,
  PromptDeliveryUncertainEventPayload,
} from './events.ts';
import type { ConversationLiveTailFrame } from './events.ts';
import { interpret_backend_event } from './logic/backend_events.ts';
import { resolve_conversation_start_request } from './logic/conversation_start_resolution.ts';
import { leading_run_that_can_share_a_turn } from './logic/held_line.ts';
import { require_message_content } from './message_content.ts';
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
import {
  acquire_after_steers,
  acquire_settled,
  contention_only_write,
} from './runtime/synchronization.ts';
import type { LockLease } from './runtime/synchronization.ts';
import {
  automatic_compaction_is_due,
  deliver_automatic_compaction,
  background_tasks_ended_with_child,
  IdleChildJanitor,
  detach_children,
  tell_agent_about_lost_background_tasks,
} from './runtime/maintenance.ts';
import {
  discarded_row,
  outcome_fate,
  prompt_row,
  refused_row,
  same_message,
  uncertain_row,
} from './runtime/messages.ts';
import { field_mutation, save_attempt } from './runtime/save_attempt.ts';
import {
  ConversationState,
  Deferred,
  own_settlement,
  same_turn,
  set_phase,
} from './runtime/state.ts';
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

export class SqliteProcessConversationSystem implements DeliveryServices, ConversationSystem {
  readonly options: ConversationCoreOptions;
  readonly workspace_admission = new WorkspaceAdmission();
  readonly start_admission: AutomaticStartAdmission;
  private readonly retiring_workspace_tickets = new Set<string>();
  private readonly retiring_workspace_conversations = new Set<string>();
  private readonly local_child_generations = new WeakMap<BackendChild, string>();
  private worker_cleanup_recheck:
    ((source: ChildReference, ticket_id: string) => Promise<CleanupResult>) | null = null;
  private readonly deferred_start_conversations = new Set<string>();
  private readonly deferred_start_preparing = new Set<string>();
  private deferred_start_timer: ReturnType<typeof setTimeout> | null = null;
  readonly conversations = new Map<string, ConversationState>();
  shutting_down = false;
  private shutdown_task: Promise<void> | null = null;
  private readonly shutdown_controller = new AbortController();
  get shutdown_signal(): AbortSignal {
    return this.shutdown_controller.signal;
  }
  private readonly native_waiters = new Set<() => void>();
  private readonly native_settlements = new Set<Promise<void>>();
  private readonly child_stop_ownership = new Map<
    import('./backends/contracts.ts').BackendChild,
    ConversationState
  >();
  private update_generation: symbol | null = null;
  private update_admission_owned = false;
  private update_metadata: ReadonlyMap<string, readonly [string, string]> | null = null;
  private update_retirement: Promise<void> | null = null;
  private update_retirement_generation: symbol | null = null;
  private update_retirement_pending = false;
  private update_stop_failed = false;
  private acknowledged_event_failures = new Map<string, number>();
  private idle_janitor: IdleChildJanitor | null = null;
  private background_tasks_left_by_previous_process: ReadonlyMap<string, readonly string[]>;
  constructor(options: ConversationCoreOptions) {
    for (const key of Object.values(ConversationBackendKey)) {
      if (!options.backend_child_factories[key])
        throw new Error(`no backend child factory for ${key}`);
    }
    if (typeof options.execution_refusal !== 'function')
      throw new Error('Conversation execution admission is required');
    if (typeof options.delivery_model_refusal !== 'function')
      throw new Error('Conversation model admission is required');
    this.options = options;
    this.start_admission = new AutomaticStartAdmission({ clock: () => this.monotonic_now() });
    const adopted = remote.initialize_retained_runs(this);
    this.background_tasks_left_by_previous_process = new Map(
      [...options.store.read_live_background_task_ids()].filter(([id]) => !adopted.has(id)),
    );
  }
  async tell_agents_of_background_tasks_lost_at_restart(): Promise<void> {
    this.require_runtime_ownership();
    const left_behind = this.background_tasks_left_by_previous_process;
    const remaining = new Map(left_behind);
    this.background_tasks_left_by_previous_process = remaining;
    for (const [conversation_id, task_ids] of [...left_behind].sort(([first], [second]) =>
      first < second ? -1 : first > second ? 1 : 0,
    )) {
      if (this.options.execution_refusal(conversation_id) !== null) continue;
      remaining.delete(conversation_id);
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
  async automatic_start_request(identity: string, conversation_id: string): Promise<StartRequest> {
    this.require_runtime_ownership();
    return this.options.store.automatic_start_request(identity, conversation_id);
  }
  accept_automatic_start(state: ConversationState, turn: TurnToken): Promise<void> {
    return this.persist(state, () =>
      this.start_admission.accept(this.options.store.start_admission_database, turn),
    );
  }
  settle_automatic_ids(
    state: ConversationState,
    identities: Iterable<string | null>,
  ): Promise<void> {
    const keys = [...identities];
    if (!this.start_admission.has_requests(keys)) return Promise.resolve();
    return this.persist(state, () =>
      this.start_admission.settle_ids(this.options.store.start_admission_database, keys),
    );
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
    state.custody_ready ??= (async () => {
      const id = state.record.conversation_id;
      state.local_child_retirement_proven =
        await this.options.store.recorded_source_retirement_proven(id);
      state.local_preparation_cancelled = !(await this.options.store.has_recorded_source(id));
      if (this.options.store.blocked_local_conversations.has(id)) state.child_is_quarantined = true;
    })();
    return state.custody_ready;
  }
  private workspace_is_retiring(state: ConversationState): boolean {
    return (
      this.retiring_workspace_conversations.has(state.record.conversation_id) ||
      this.retiring_workspace_tickets.has(
        new Map(state.record.identity_environment_variables).get('PLAN_TICKET_ID') ?? '',
      )
    );
  }
  runtime_turn(conversation_id: string): RunningTurn | null {
    return this.conversations.get(conversation_id)?.running_turn ?? null;
  }
  async start_conversation(request: ConversationStartRequest): Promise<void> {
    return this.workspace_admission.operation(
      async (admitted) => {
        if (!admitted) throw new Error('Workspace admission is closed for an owner update.');
        this.require_runtime_ownership();
        const refusal = this.options.execution_refusal(request.conversation_id);
        if (refusal !== null) throw refusal;
        const resolved = resolve_conversation_start_request(request);
        if (
          this.retiring_workspace_tickets.has(
            new Map(resolved.role_materials?.identity_environment_variables ?? []).get(
              'PLAN_TICKET_ID',
            ) ?? '',
          )
        )
          throw new Error('Ticket workspace admission is closed for retirement.');
        // The record is the first effect, before an in-memory identity can exist.
        const record = this.options.store.create_conversation(resolved);
        if (!this.conversations.has(record.conversation_id)) {
          const state = new ConversationState(record);
          state.local_child_retirement_proven = true;
          state.custody_ready = Promise.resolve();
          this.conversations.set(record.conversation_id, state);
        }
      },
      { starts: true },
    );
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
      { shutdown_signal: this.shutdown_controller.signal, retry_later_at },
    );
    return task.finally(() => tracked?.close());
  }
  native_operation<T>(operation: () => Promise<T>): Promise<T> {
    if (this.shutting_down)
      return Promise.reject(new DOMException('The operation was aborted', 'AbortError'));
    let cancel!: () => void;
    const closing = new Promise<T>((_resolve, reject) => {
      cancel = () => reject(new DOMException('The operation was aborted', 'AbortError'));
    });
    this.native_waiters.add(cancel);
    // Reserve before invoking a provider: it can synchronously request shutdown.
    let release!: () => void;
    const actual = new Promise<void>((resolve) => {
      release = resolve;
    });
    this.native_settlements.add(actual);
    const settled = () => {
      this.native_settlements.delete(actual);
      release();
    };
    let native: Promise<T>;
    try {
      native = operation();
    } catch (error) {
      settled();
      this.native_waiters.delete(cancel);
      return Promise.reject(error);
    }
    void Promise.resolve(native).then(settled, settled);
    return Promise.race([native, closing]).finally(() => this.native_waiters.delete(cancel));
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
    this.options.publish_frame?.(state.record.conversation_id, new HeldPromptsChangedFrame());
  }
  async send(
    conversation_id: string,
    content: MessageContent,
    options: SendOptions,
  ): Promise<PromptDeliveryFate> {
    return this.workspace_admission.operation(
      async (admitted) => {
        if (!admitted) throw new Error('Workspace admission is closed for an owner update.');
        if (options.record_refusal && options.sender_message_id == null)
          throw new Error('record_refusal requires a sender_message_id');
        this.require_runtime_ownership();
        const refusal = this.options.execution_refusal(conversation_id);
        if (refusal !== null) throw refusal;
        const state = this.conversation_state(conversation_id);
        if (!state) return new PromptDeliveryRefused({ refusal_reason: R.no_such_conversation });
        await this.ensure_local_custody(state);
        if (this.workspace_is_retiring(state))
          throw new Error('Ticket workspace admission is closed for retirement.');
        return own_settlement(
          state,
          this.send_owned(state, require_message_content(content), options),
        );
      },
      { starts: true },
    );
  }
  private async send_owned(
    state: ConversationState,
    content: MessageContent,
    options: SendOptions,
  ): Promise<PromptDeliveryFate> {
    const mode = options.mode ?? PromptDeliveryMode.queue;
    state.last_touched_monotonic = this.monotonic_now();
    const boundary = await state.lock.run(() => {
      if (options.owner_read_through_sequence != null) {
        if (options.sender?.kind !== 'owner')
          throw new Error('only the owner can acknowledge messages with a reply');
        if (
          options.owner_read_through_sequence < 0 ||
          options.owner_read_through_sequence > state.record.latest_sequence
        )
          throw new Error('owner read boundary is outside this conversation');
        return options.owner_read_through_sequence;
      }
      return options.sender?.kind === 'owner' ? state.record.latest_sequence : null;
    });
    const message: Message = {
      held_prompt_id: '',
      content,
      sender_label: options.sender_label,
      sender: options.sender ?? null,
      recipient: options.recipient ?? null,
      sender_message_id: options.sender_message_id ?? null,
      sent_at_unix_milliseconds: options.sent_at_unix_milliseconds ?? null,
      snapshot_sent_at_unix_milliseconds: 0,
      model_change: options.model_change ?? null,
      reasoning_effort_change: options.reasoning_effort_change ?? null,
      reasoning_effort_is_set: options.reasoning_effort_is_set ?? false,
      owner_read_through_sequence: boundary,
      acknowledge_owner_on_failure: options.owner_read_through_sequence == null,
      recovery_references: options.recovery_references ?? [],
      recording_receipt: options.recording_receipt ?? null,
      queue_reason: PromptQueueReason.requested,
      delivery_uncertain: false,
      system_generated: options.sender == null,
    };
    const duplicate = await this.admit_sender(state, message);
    if (duplicate) {
      this.start_admission.reuse_outcome(message.sender_message_id, {
        queued: duplicate instanceof PromptDeliveryQueued,
      });
      return duplicate;
    }
    this.start_admission.handoff(message.sender_message_id);
    try {
      const fate =
        mode === PromptDeliveryMode.steer
          ? await controls.steer(this, state, message)
          : mode === PromptDeliveryMode.send_now
            ? await controls.send_now(this, state, message)
            : await this.queue(state, message);
      if (
        options.record_refusal &&
        fate instanceof PromptDeliveryRefused &&
        message.sender_message_id !== null &&
        this.options.store.sender_message_outcome(
          state.record.conversation_id,
          message.sender_message_id,
        ) === null
      ) {
        await state.lock.run(async () => {
          const rows = await this.persist(state, () =>
            this.options.store.append_events(
              state.record.conversation_id,
              [refused_row(message, mode, fate.refusal_reason)],
              {
                recording_receipt: message.recording_receipt,
                owner_read_through_sequence: message.acknowledge_owner_on_failure ? boundary : null,
              },
            ),
          );
          this.note_written(state, rows);
        });
      }
      if (
        !message.acknowledge_owner_on_failure &&
        boundary !== null &&
        fate instanceof PromptDeliveryQueued
      ) {
        await state.lock.run(async () => {
          const record = await this.persist(state, () =>
            this.options.store.advance_owner_read_through_sequence(
              state.record.conversation_id,
              boundary,
            ),
          );
          if (record)
            state.record = new ConversationRecord({
              ...state.record,
              owner_read_through_sequence: Math.max(
                state.record.owner_read_through_sequence,
                record.owner_read_through_sequence,
              ),
            });
        });
      }
      if (
        fate instanceof PromptDeliveryRefused ||
        fate instanceof PromptDeliverySuppressed ||
        fate instanceof PromptDeliveryInjected
      )
        await this.settle_automatic_ids(state, [message.sender_message_id]);
      else if (fate instanceof PromptDeliveryUncertain) {
        if (this.start_admission.attempted([message.sender_message_id])) {
          if (message.sender_message_id !== null)
            this.start_admission.uncertain(message.sender_message_id);
        } else await this.settle_automatic_ids(state, [message.sender_message_id]);
      }
      if (
        fate instanceof PromptDeliveryQueued &&
        state.phase === 'idle' &&
        !state.child_is_quarantined
      )
        this.schedule_drain(state);
      return fate;
    } catch (error) {
      await this.persist(state, () =>
        this.start_admission.failed_sender(
          this.options.store.start_admission_database,
          message.sender_message_id,
        ),
      );
      throw error;
    } finally {
      if (message.sender_message_id !== null) {
        await state.lock.run(() => {
          const admission = state.admitted_sender_messages.get(message.sender_message_id!);
          state.admitted_sender_messages.delete(message.sender_message_id!);
          admission?.settled.resolve();
        });
      }
    }
  }
  private async admit_sender(
    state: ConversationState,
    message: Message,
  ): Promise<PromptDeliveryFate | null> {
    const identity = message.sender_message_id;
    if (identity === null) return null;
    while (true) {
      const observation = await state.lock.run(() => {
        const admission =
          state.sender_messages_being_delivered.get(identity) ??
          state.admitted_sender_messages.get(identity);
        if (admission) {
          if (!same_message(message, admission.message))
            throw new Error('sender_message_id already names a different message');
          return {
            wait: admission.settled.promise,
            fate: null,
            sequence: state.record.latest_sequence,
          };
        }
        const index = state.held_prompts.findIndex((held) => held.sender_message_id === identity);
        if (index >= 0) {
          if (!same_message(message, state.held_prompts[index], true))
            throw new Error('sender_message_id already names a different message');
          return {
            wait: null,
            fate: new PromptDeliveryQueued({ queue_position: index + 1 }),
            sequence: state.record.latest_sequence,
          };
        }
        return { wait: null, fate: null, sequence: state.record.latest_sequence };
      });
      if (observation.wait) {
        await observation.wait;
        continue;
      }
      if (observation.fate) return observation.fate;
      const outcome = this.options.store.sender_message_outcome(
        state.record.conversation_id,
        identity,
      );
      if (outcome) {
        if (!same_message(message, outcome.payload))
          throw new Error('sender_message_id already names a different message');
        return outcome_fate(outcome.payload);
      }
      const admitted = await state.lock.run(() => {
        if (
          state.record.latest_sequence !== observation.sequence ||
          state.admitted_sender_messages.has(identity) ||
          state.sender_messages_being_delivered.has(identity) ||
          state.held_prompts.some((held) => held.sender_message_id === identity)
        )
          return false;
        state.admitted_sender_messages.set(identity, { message, settled: new Deferred<void>() });
        return true;
      });
      if (admitted) return null;
    }
  }
  async queue(state: ConversationState, message: Message): Promise<PromptDeliveryFate> {
    const admission = await state.lock.run(async () => {
      if (
        state.being_killed ||
        state.child_is_quarantined ||
        state.phase !== 'idle' ||
        state.held_prompts.length > 0 ||
        state.late_steers.size > 0
      )
        return this.hold_prompt(state, message);
      if (await this.automatic_compaction_may_start(state)) {
        const fate = this.hold_prompt(state, message);
        return { automatic: await this.reserve_turn(state, true), fate };
      }
      if (!this.start_admission.begin_queued([message.sender_message_id])) {
        const fate = this.hold_prompt(state, message);
        this.defer_start_drain(state);
        return fate;
      }
      return this.reserve_turn(state, false, [message.sender_message_id]);
    });
    if (admission instanceof PromptDeliveryQueued) return admission;
    if ('automatic' in admission) {
      await this.deliver_automatic_compaction(state, admission.automatic);
      return admission.fate;
    }
    return this.deliver_and_finalize(state, admission, [message], false);
  }
  hold_prompt(
    state: ConversationState,
    message: Message,
    position = state.held_prompts.length,
  ): PromptDeliveryQueued {
    const held: Message = {
      ...message,
      held_prompt_id: `held_${randomUUID().replaceAll('-', '')}`,
      snapshot_sent_at_unix_milliseconds:
        message.sent_at_unix_milliseconds ?? Math.floor(this.unix_time_now() * 1000),
    };
    this.start_admission.queue(message.sender_message_id);
    state.held_prompts.splice(position, 0, held);
    this.mark_eligible_held(state);
    this.publish_held_prompts_changed(state);
    return new PromptDeliveryQueued({ queue_position: position + 1 });
  }
  async reserve_turn(
    state: ConversationState,
    automatic_compaction = false,
    sender_message_ids: readonly (string | null)[] = [],
  ): Promise<ReservedTurn> {
    const requests = this.start_admission.requests_for(sender_message_ids);
    const number = await this.persist(state, () =>
      this.options.store.reserve_turn(state.record.conversation_id, requests),
    );
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
  async deliver_and_finalize(
    state: ConversationState,
    reservation: ReservedTurn,
    batch: readonly Message[],
    queued: boolean,
    drain_after_delivery_exception = true,
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
      if (error instanceof PromptWriteFailed) {
        await this.record_delivery_uncertainty(state, reservation, batch);
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
          for (const message of [...batch].reverse())
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
        const written = await this.persist(
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
      this.finish_held_deliveries(state, batch);
      const present = new Set(state.held_prompts.map((message) => message.held_prompt_id));
      for (const message of [...batch].reverse()) {
        if (message.held_prompt_id && !present.has(message.held_prompt_id))
          state.held_prompts.unshift({ ...message, delivery_uncertain: true });
      }
      set_phase(state, 'idle');
      this.mark_eligible_held(state);
      this.publish_held_prompts_changed(state);
    });
  }
  finish_held_deliveries(state: ConversationState, batch: readonly Message[]): void {
    for (const message of batch) {
      if (message.sender_message_id === null) continue;
      const admission = state.sender_messages_being_delivered.get(message.sender_message_id);
      state.sender_messages_being_delivered.delete(message.sender_message_id);
      admission?.settled.resolve();
    }
  }
  private deprioritize_held(state: ConversationState): void {
    for (const message of state.held_prompts)
      if (!message.delivery_uncertain)
        this.start_admission.wait_if_unadmitted(message.sender_message_id);
  }
  private mark_eligible_held(state: ConversationState): void {
    if (
      !['idle', 'draining'].includes(state.phase) ||
      state.child_is_quarantined ||
      state.late_steers.size ||
      state.held_prompts.some((message) => message.delivery_uncertain)
    ) {
      this.deprioritize_held(state);
      return;
    }
    const head = leading_run_that_can_share_a_turn(state.held_prompts);
    for (const message of state.held_prompts.slice(head.length))
      this.start_admission.wait_if_unadmitted(message.sender_message_id);
    this.start_admission.mark_eligible(head.map((message) => message.sender_message_id));
  }
  defer_start_drain(state: ConversationState): void {
    if (this.shutting_down || state.child_is_quarantined) return;
    this.deferred_start_conversations.add(state.record.conversation_id);
    if (this.deferred_start_timer !== null) return;
    this.deferred_start_timer = setTimeout(
      () => {
        this.deferred_start_timer = null;
        if (this.shutting_down) return;
        const pending = [...this.deferred_start_conversations].flatMap((identity) => {
          const current = this.conversations.get(identity);
          return current ? [current] : [];
        });
        this.deferred_start_conversations.clear();
        for (const current of pending) this.mark_eligible_held(current);
        pending.sort((first, second) => {
          const a = this.start_admission.priority(
            first.held_prompts.map((message) => message.sender_message_id),
          );
          const b = this.start_admission.priority(
            second.held_prompts.map((message) => message.sender_message_id),
          );
          return a[0] - b[0] || a[1] - b[1];
        });
        for (const current of pending) {
          const identity = current.record.conversation_id;
          if (
            this.deferred_start_preparing.has(identity) ||
            this.deferred_start_preparing.size >= this.start_admission.limit
          ) {
            this.defer_start_drain(current);
            continue;
          }
          this.deferred_start_preparing.add(identity);
          void own_settlement(current, this.drain_held_prompts(current))
            .finally(() => {
              this.deferred_start_preparing.delete(identity);
            })
            .catch((error) =>
              console.error(`conversation ${identity} could not retry its deferred start`, error),
            );
        }
      },
      this.start_admission.poll_delay(1) * 1000,
    );
  }
  schedule_drain(state: ConversationState): void {
    if (this.shutting_down) return;
    this.deferred_start_conversations.delete(state.record.conversation_id);
    this.mark_eligible_held(state);
    void own_settlement(state, this.drain_held_prompts(state)).catch((error) =>
      console.error(
        `conversation ${state.record.conversation_id} could not drain held prompts`,
        error,
      ),
    );
  }
  async drain_held_prompts(state: ConversationState): Promise<void> {
    if (this.shutting_down) return;
    await this.ensure_local_custody(state);
    if (
      this.workspace_is_retiring(state) ||
      this.options.store.blocked_local_conversations.has(state.record.conversation_id)
    )
      return;
    this.require_runtime_ownership();
    if (this.options.execution_refusal(state.record.conversation_id) !== null) {
      this.deprioritize_held(state);
      return;
    }
    const taken = await state.lock.run(() => {
      if (
        state.being_killed ||
        state.child_is_quarantined ||
        state.phase !== 'idle' ||
        state.held_prompts.length === 0 ||
        state.held_prompts.some((held) => held.delivery_uncertain) ||
        state.late_steers.size > 0
      )
        return false;
      set_phase(state, 'draining');
      return true;
    });
    if (!taken) {
      if (state.phase !== 'draining') this.deprioritize_held(state);
      return;
    }
    while (true) {
      const next = await state.lock.run(async () => {
        if (
          this.shutting_down ||
          this.options.execution_refusal(state.record.conversation_id) !== null
        ) {
          set_phase(state, 'idle');
          this.deprioritize_held(state);
          return null;
        }
        if (state.being_killed || state.child_is_quarantined || state.held_prompts.length === 0) {
          set_phase(state, 'idle');
          this.mark_eligible_held(state);
          return null;
        }
        const batch = leading_run_that_can_share_a_turn(state.held_prompts);
        this.mark_eligible_held(state);
        if (!this.start_admission.begin_queued(batch.map((message) => message.sender_message_id))) {
          set_phase(state, 'idle');
          this.defer_start_drain(state);
          return null;
        }
        try {
          await this.persist(state, () =>
            this.options.store.mark_held_sender_messages_leaving_queue(
              state.record.conversation_id,
              batch.flatMap((message) =>
                message.sender_message_id ? [message.sender_message_id] : [],
              ),
            ),
          );
          for (const message of batch) {
            if (message.sender_message_id !== null)
              state.sender_messages_being_delivered.set(message.sender_message_id, {
                message,
                settled: new Deferred<void>(),
              });
          }
          state.held_prompts.splice(0, batch.length);
          this.publish_held_prompts_changed(state);
          return {
            batch,
            reservation: await this.reserve_turn(
              state,
              false,
              batch.map((message) => message.sender_message_id),
            ),
          };
        } catch (error) {
          this.finish_held_deliveries(state, batch);
          for (const message of [...batch].reverse()) {
            this.start_admission.queue(message.sender_message_id);
            if (!state.held_prompts.includes(message)) state.held_prompts.unshift(message);
          }
          set_phase(state, 'idle');
          this.mark_eligible_held(state);
          this.defer_start_drain(state);
          throw error;
        }
      });
      if (!next) return;
      const fate = await this.deliver_and_finalize(state, next.reservation, next.batch, true);
      if (fate instanceof PromptDeliveryStarted || fate instanceof PromptDeliveryUncertain) return;
    }
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
            if (event instanceof AgentMessageCompleted) return;
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
  async record_local_child_preparation(
    state: ConversationState,
    child: BackendChild,
    sink: CoreBackendEventSink,
  ): Promise<void> {
    await this.ensure_local_custody(state);
    state.local_child_retirement_proven = false;
    state.local_preparation_cancelled = false;
    this.local_child_generations.set(child, sink.generation);
    await this.options.store.record_child_custody(state.record.conversation_id, sink.generation);
  }
  async check_local_child_started(state: ConversationState, turn: TurnToken): Promise<boolean> {
    const child = state.child;
    const generation = child === null ? undefined : this.local_child_generations.get(child);
    return (
      generation !== undefined &&
      (await this.options.store.check_local_child_started(
        state.record.conversation_id,
        generation,
        turn.turn_number,
      ))
    );
  }
  async _confirm_local_child_stopped(state: ConversationState, child: BackendChild): Promise<void> {
    const generation = this.local_child_generations.get(child);
    if (!child.stop_outside_lock && generation !== undefined)
      await this.options.store.record_child_custody(
        state.record.conversation_id,
        generation,
        null,
        { retired: true },
      );
    await this.persist(state, () =>
      this.start_admission.retire_source(this.options.store.start_admission_database, child),
    );
    state.local_child_retirement_proven = true;
    state.child_event_sink = null;
    this.child_stop_ownership.delete(child);
    await background_tasks_ended_with_child(this, state);
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
  async begin_attempt(
    state: ConversationState,
    token: TurnToken,
    operation: LifecycleFact['operation'],
    provider_identity: string | null = null,
    related: readonly LifecycleFact[] = [],
    mutations: readonly RuntimeMutation[] = [],
  ): Promise<LifecycleFact> {
    const attempt: LifecycleFact = {
      kind: K.attempt,
      identity: randomUUID().replaceAll('-', ''),
      turn_number: token.turn_number,
      operation,
      status: S.intent,
      provider_identity,
    };
    await this.persist(
      state,
      () =>
        this.options.store.transition_lifecycle(state.record.conversation_id, [
          attempt,
          ...related,
        ]),
      mutations,
    );
    return attempt;
  }
  async revoke_turn_requests(state: ConversationState, running: RunningTurn): Promise<void> {
    const facts = this.options.store
      .lifecycle_facts(state.record.conversation_id)
      .filter(
        (fact) =>
          fact.kind === K.request &&
          fact.turn_number === running.token.turn_number &&
          (fact.status === S.pending || fact.status === S.answering),
      )
      .map((fact) => ({ ...fact, status: S.unavailable }));
    await this.persist(state, () =>
      this.options.store.transition_lifecycle(state.record.conversation_id, facts),
    );
    running.callbacks_revoked = true;
  }
  async end_turn(
    state: ConversationState,
    running: RunningTurn,
    ending: import('./events.ts').ConversationTurnEnding,
    error: string | null,
    cancel_fact: LifecycleFact | null = null,
  ): Promise<StoredConversationEvent | null> {
    if (running.ended) return null;
    await this.revoke_turn_requests(state, running);
    const interpreted = interpret_backend_event(
      running.token,
      new TurnEnded({ ending, error_summary: error }),
      running,
      this.options.store.lifecycle_facts(state.record.conversation_id),
    );
    const written = await this.persist(
      state,
      () =>
        this.options.store.append_turn_ending(state.record.conversation_id, interpreted.payloads, {
          lifecycle: [
            interpreted.lifecycle[0],
            ...(cancel_fact ? [cancel_fact] : []),
            ...interpreted.lifecycle.slice(1),
          ],
          agent_activity: !turn_is_maintenance(running),
          automatic_compaction_confirmed: running.compaction_confirmed,
          working_turn_compaction_confirmed:
            running.compaction_confirmed && !running.automatic_compaction,
          automatic_compaction_result: interpreted.compaction_result,
          create_failure_notice: interpreted.failure_notice,
        }),
      [
        field_mutation(running, 'ended', true),
        field_mutation(running, 'pending_permission_ask_ids', new Set()),
        field_mutation(running, 'pending_user_input_questions', new Map()),
        field_mutation(running, 'permission_provider_ids', new Map()),
        field_mutation(running, 'user_input_provider_ids', new Map()),
        field_mutation(state, 'last_ended_turn_token', running.token),
        field_mutation(
          state,
          'last_ended_turn_was_automatic_compaction',
          running.automatic_compaction,
        ),
        field_mutation(state, 'running_turn', null),
      ],
    );
    this.note_written(state, written);
    if (written.length > 0) {
      const last = written[written.length - 1];
      const latest_agent_activity_sequence = turn_is_maintenance(running)
        ? state.record.latest_agent_activity_sequence
        : last.sequence;
      state.record = new ConversationRecord({
        ...state.record,
        latest_agent_activity_at: turn_is_maintenance(running)
          ? state.record.latest_agent_activity_at
          : last.created_at,
        latest_agent_activity_sequence,
        automatically_compacted_through_sequence: running.compaction_confirmed
          ? latest_agent_activity_sequence
          : state.record.automatically_compacted_through_sequence,
        working_turn_compacted_through_sequence:
          running.compaction_confirmed && !running.automatic_compaction
            ? latest_agent_activity_sequence
            : state.record.working_turn_compacted_through_sequence,
        automatic_compaction_attempted_through_sequence:
          running.compaction_confirmed || interpreted.compaction_result !== null
            ? latest_agent_activity_sequence
            : state.record.automatic_compaction_attempted_through_sequence,
      });
    }
    if (!this.shutting_down) this.options.emit_change?.();
    return written[written.length - 1] ?? null;
  }
  acquire_after_steers(state: ConversationState): Promise<LockLease> {
    return acquire_after_steers(state);
  }
  acquire_settled(state: ConversationState): Promise<LockLease> {
    return acquire_settled(state);
  }
  async discard_child(
    state: ConversationState,
    child: import('./backends/contracts.ts').BackendChild,
  ): Promise<boolean> {
    if (child.stop_outside_lock) {
      remote.schedule_child_retirement(this, state, child);
      return false;
    }
    // A pending stop remains owned even when the active slot becomes available.
    this.child_stop_ownership.set(child, state);
    if (state.child === child) state.child = null;
    let stopped = false;
    try {
      await this.native_operation(() => child.stop());
      await child.settled?.();
      await this._confirm_local_child_stopped(state, child);
      this.child_stop_ownership.delete(child);
      state.child_is_quarantined = false;
      stopped = true;
    } catch (error) {
      if (state.child === null) state.child = child;
      state.child_is_quarantined = true;
      console.error(`conversation ${state.record.conversation_id} could not stop its child`, error);
    }
    return stopped;
  }
  retire_child_for_rebind(state: ConversationState, child: BackendChild): Promise<boolean> {
    return child.stop_outside_lock
      ? remote.schedule_child_retirement(this, state, child, false)
      : this.discard_child(state, child);
  }
  async automatic_compaction_may_start(state: ConversationState): Promise<boolean> {
    return (
      this.automatic_compaction_is_due(state) &&
      this.options.store.automated_maintenance_suppression_reason(state.record.conversation_id) ===
        null
    );
  }
  automatic_compaction_is_due(state: ConversationState): boolean {
    return automatic_compaction_is_due(this, state);
  }
  deliver_automatic_compaction(state: ConversationState, reservation: ReservedTurn): Promise<void> {
    return deliver_automatic_compaction(this, state, reservation);
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
  configure_worker_cleanup_recheck(
    callback: (source: ChildReference, ticket_id: string) => Promise<CleanupResult>,
  ): void {
    this.worker_cleanup_recheck = callback;
  }
  async recheck_worker_cleanup(ticket_id: string): Promise<CleanupResult> {
    this.require_runtime_ownership();
    const ticket = this.options.store.start_admission_database.read((sql) => {
      const row = sql.get<{ machine_id: string; conversation_id: string | null }>(
        'SELECT machine_id,conversation_id FROM tickets WHERE id=?',
        [ticket_id],
      );
      if (row === undefined) return null;
      const local_ids = sql
        .all<{ conversation_id: string }>(
          'SELECT w.conversation_id FROM ticket_workspace_conversations w JOIN conversation_child_custody c USING(conversation_id) WHERE w.ticket_id=?',
          [ticket_id],
        )
        .map((item) => item.conversation_id);
      const presence = sql.get<{ lease_json: string | null; last_lease_at_ms: number | null }>(
        'SELECT lease_json,last_lease_at_ms FROM mac_executor_presence WHERE machine_id=?',
        [row.machine_id],
      );
      let lease: WireValue<'LeaseRequest'> | null = null;
      let fresh = false;
      if (presence?.lease_json !== null && presence?.lease_json !== undefined) {
        try {
          lease = decode_json(presence.lease_json, 'LeaseRequest');
          fresh =
            lease.identity.protocol_version === PROTOCOL_VERSION &&
            presence.last_lease_at_ms !== null &&
            Math.floor(this.unix_time_now() * 1000) - presence.last_lease_at_ms <=
              LEASE_CONTINUITY_SECONDS * 1000;
        } catch {
          /* Invalid lease evidence cannot prove cleanup. */
        }
      }
      const uncertain = new Set(
        (lease?.cleanup_uncertainties ?? [])
          .filter((item) => item.ticket_id === ticket_id)
          .map((item) => item.conversation_id),
      );
      const historical = sql
        .all<{ executor_incarnation: string; child_id: string; conversation_id: string }>(
          "SELECT executor_incarnation,child_id,conversation_id FROM executor_child_assignments WHERE principal_kind='ticket' AND principal_id=?",
          [ticket_id],
        )
        .filter((item) => uncertain.has(item.conversation_id) || uncertain.has(''));
      return { ...row, local_ids, historical, fresh: fresh && uncertain.size === 0 };
    });
    if (ticket === null) return cleanup_result(false, 'The Ticket is unavailable');
    if (ticket.machine_id !== HOME_MACHINE_ID) {
      const state =
        ticket.conversation_id === null ? null : this.conversation_state(ticket.conversation_id);
      const source =
        state === null
          ? null
          : (this.retained_run(state)?.reference ?? state.retained_child_reference);
      const source_key = (item: ChildReference) =>
        JSON.stringify([item.executor_incarnation, item.child_id, item.conversation_id]);
      const sources = new Map(ticket.historical.map((item) => [source_key(item), item]));
      if (source !== null) sources.set(source_key(source), source);
      if (sources.size === 0)
        return cleanup_result(
          ticket.fresh,
          ticket.fresh
            ? 'No retained Worker source needs cleanup'
            : 'Fresh executor cleanup evidence is unavailable',
        );
      if (this.worker_cleanup_recheck === null)
        return cleanup_result(false, 'The executor cleanup checker is unavailable');
      for (const recorded of sources.values()) {
        const result = await this.worker_cleanup_recheck(recorded, ticket_id);
        if (!result.complete) return result;
      }
      return cleanup_result(true, 'Recorded Worker cleanup is complete');
    }
    const ids = new Set(ticket.local_ids);
    if (ticket.conversation_id !== null) ids.add(ticket.conversation_id);
    for (const id of ids) {
      const state = this.conversation_state(id);
      if (state === null) return cleanup_result(false, 'The recorded conversation is unavailable');
      await this.ensure_local_custody(state);
      const result = await state.lock.run(async () => {
        if (this.options.store.blocked_local_conversations.has(id)) {
          const settlement = await this.options.store.settle_recorded_source(id, {
            inspect_only: true,
          });
          if (settlement === null)
            return cleanup_result(false, 'The recorded Worker cleanup is not proven');
          await this.take_recorded_settlement(state, settlement);
          return null;
        }
        if (state.running_turn !== null || state.reserved_turn !== null)
          return cleanup_result(false, 'The Worker still has an active turn');
        if (!(await this.options.store.has_recorded_source(id))) {
          return state.child !== null || state.child_is_quarantined
            ? cleanup_result(false, 'The retained Worker has no exact cleanup evidence')
            : null;
        }
        if (!(await this.options.store.retire_idle_recorded_source(id, { inspect_only: true })))
          return cleanup_result(
            false,
            'Worker cleanup is not proven. Stop the old process and recheck cleanup.',
          );
        if (state.child !== null) {
          await this.discard_child(state, state.child);
          if (state.child !== null || state.child_is_quarantined)
            return cleanup_result(false, 'The retained Worker cleanup is not proven');
        }
        state.local_child_retirement_proven = true;
        state.child_is_quarantined = false;
        return null;
      });
      if (result !== null) return result;
    }
    return cleanup_result(true, 'Recorded Worker cleanup is complete');
  }
  private async take_recorded_settlement(
    state: ConversationState,
    settlement: 'undelivered' | 'retired',
  ): Promise<void> {
    state.local_child_retirement_proven = settlement === 'retired';
    state.local_preparation_cancelled = settlement === 'undelivered';
    state.child_is_quarantined = false;
    const token = this.options.store.settled_recorded_turn(state.record.conversation_id);
    if (token !== null)
      await this.persist(state, () =>
        this.start_admission.reject(this.options.store.start_admission_database, token),
      );
  }
  async confirm_killed_source(state: ConversationState): Promise<void> {
    const id = state.record.conversation_id;
    if (this.options.store.blocked_local_conversations.has(id)) {
      const settlement = await this.options.store.settle_recorded_source(id);
      if (settlement === null)
        throw new PlannerError(
          'retry_later',
          `${await this.options.store.recorded_source_block_reason(id)} Recheck Worker cleanup.`,
        );
      await this.take_recorded_settlement(state, settlement);
    }
    if (
      state.child === null &&
      !state.local_preparation_cancelled &&
      !state.local_child_retirement_proven &&
      (await this.options.store.has_recorded_source(id))
    ) {
      if (!(await this.options.store.retire_idle_recorded_source(id)))
        throw new PlannerError(
          'retry_later',
          'The recorded Worker cleanup is not proven. Recheck Worker cleanup.',
        );
      state.local_child_retirement_proven = true;
    }
    const remote_receipt = this.retained_run(state)?.retirement_confirmed === true;
    await state.lock.run(() => {
      if (
        state.child !== null ||
        state.retained_child_reference !== null ||
        state.child_is_quarantined ||
        (state.retirement_task !== null && state.retiring_child !== null) ||
        !(
          remote_receipt ||
          state.local_child_retirement_proven ||
          state.local_preparation_cancelled
        )
      )
        throw new PlannerError(
          'retry_later',
          'The old Worker is not proven ended. Recheck Worker cleanup.',
        );
    });
  }
  async promote_held_prompt(
    conversation_id: string,
    held_prompt_id: string,
    mode: HeldPromptPromotionMode,
  ): Promise<PromptDeliveryFate | null> {
    this.require_runtime_ownership();
    const refusal = this.options.execution_refusal(conversation_id);
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
  async pending_permission_ask_ids(conversation_id: string): Promise<ReadonlySet<string>> {
    const state = this.conversation_state(conversation_id);
    this.options.require_signal_current?.();
    return new Set(state?.running_turn?.pending_permission_ask_ids ?? []);
  }
  async pending_user_input_request_ids(conversation_id: string): Promise<ReadonlySet<string>> {
    const state = this.conversation_state(conversation_id);
    this.options.require_signal_current?.();
    return new Set(state?.running_turn?.pending_user_input_questions.keys() ?? []);
  }
  async run_if_idle(conversation_id: string, operation: () => void): Promise<boolean> {
    const state = this.conversation_state(conversation_id);
    if (!state) {
      operation();
      return true;
    }
    if (state.lock.locked) return false;
    return state.lock.run(() => {
      if (
        state.phase !== 'idle' ||
        state.reserved_turn ||
        state.running_turn ||
        state.held_prompts.length ||
        state.admitted_sender_messages.size ||
        state.sender_messages_being_delivered.size ||
        state.steer_settlements.size ||
        state.settlement_tasks.size
      )
        return false;
      operation();
      return true;
    });
  }
  async record_events_transaction<T>(
    conversation_id: string,
    operation: (
      tx: import('../core/database/contracts.ts').TransactionSql,
    ) => import('./record/contracts.ts').EventMutation<T>,
  ): Promise<T> {
    return this.workspace_admission.operation(async (admitted) => {
      if (!admitted) throw new Error('Workspace admission is closed for an owner update.');
      const state = this.conversation_state(conversation_id);
      if (!state) throw new Error('conversation is absent');
      return own_settlement(
        state,
        state.lock.run(async () => {
          if (this.shutting_down) throw new DOMException('The operation was aborted', 'AbortError');
          const result = await contention_only_write(() =>
            this.options.store.transact_events(conversation_id, operation),
          );
          this.note_written(state, result.written);
          return result.value;
        }),
      );
    });
  }
  async held_prompts(conversation_id: string): Promise<readonly HeldPrompt[]> {
    const state = this.conversation_state(conversation_id);
    if (!state) return [];
    return state.lock.run(() =>
      state.held_prompts.map(
        (message) =>
          new HeldPrompt({
            ...message,
            sent_at_unix_milliseconds: message.snapshot_sent_at_unix_milliseconds,
          }),
      ),
    );
  }
  async sender_message_ids_awaiting_outcome(conversation_id: string): Promise<ReadonlySet<string>> {
    const state = this.conversation_state(conversation_id);
    if (!state) return new Set();
    // Queue removal and delivery registration share one synchronous step.
    return new Set([
      ...state.held_prompts.flatMap((message) =>
        message.sender_message_id ? [message.sender_message_id] : [],
      ),
      ...state.admitted_sender_messages.keys(),
      ...state.sender_messages_being_delivered.keys(),
    ]);
  }
  async is_running(conversation_id: string): Promise<boolean> {
    this.require_runtime_ownership();
    const state = this.conversations.get(conversation_id);
    this.options.require_signal_current?.();
    return state?.running_turn != null;
  }
  async has_pending_permission_ask(conversation_id: string): Promise<boolean> {
    this.require_runtime_ownership();
    const state = this.conversations.get(conversation_id);
    this.options.require_signal_current?.();
    return (state?.running_turn?.pending_permission_ask_ids.size ?? 0) > 0;
  }
  async has_pending_user_input(conversation_id: string): Promise<boolean> {
    this.require_runtime_ownership();
    const state = this.conversations.get(conversation_id);
    this.options.require_signal_current?.();
    return (state?.running_turn?.pending_user_input_questions.size ?? 0) > 0;
  }
  async record_message_to_owner(
    conversation_id: string,
    content: MessageContent,
    options: RecordMessageToOwnerOptions,
  ): Promise<void> {
    require_message_content(content);
    const state = this.conversation_state(conversation_id);
    if (!state) throw new Error('no such conversation');
    return own_settlement(
      state,
      state.lock.run(async () => {
        if (options.sender_message_id != null) {
          const outcome = this.options.store.sender_message_outcome(
            conversation_id,
            options.sender_message_id,
          );
          if (outcome) {
            if (
              !(outcome.payload instanceof MessageToOwnerEventPayload) ||
              !isDeepStrictEqual(
                [
                  outcome.payload.content,
                  outcome.payload.sender_label,
                  outcome.payload.sender,
                  outcome.payload.recipient,
                ],
                [content, options.sender_label, options.sender, options.recipient],
              )
            )
              throw new Error('sender_message_id already names a different message');
            return;
          }
        }
        const rows = await this.persist(state, () =>
          this.options.store.append_events(
            conversation_id,
            [new MessageToOwnerEventPayload({ ...options, content })],
            { recording_receipt: options.recording_receipt },
          ),
        );
        this.note_written(state, rows);
      }),
    );
  }
  workspace_activity(): 'idle' | 'busy' | 'unknown' {
    if (
      this.shutting_down ||
      this.workspace_admission.closed ||
      this.options.store.blocked_local_conversations.size
    )
      return 'unknown';
    if (this.workspace_admission.active) return 'busy';
    for (const state of this.conversations.values()) {
      if (
        state.lock.locked ||
        state.phase !== 'idle' ||
        state.reserved_turn ||
        state.running_turn ||
        state.held_prompts.length ||
        state.admitted_sender_messages.size ||
        state.sender_messages_being_delivered.size ||
        state.steer_settlements.size ||
        state.child_is_quarantined ||
        state.settlement_tasks.size
      )
        return 'busy';
    }
    return this.options.store.read_live_background_task_ids().size > 0 ? 'busy' : 'idle';
  }
  private state_has_update_work(state: ConversationState): boolean {
    return !!(
      state.lock.locked ||
      state.phase !== 'idle' ||
      state.reserved_turn ||
      state.running_turn ||
      state.held_prompts.length ||
      state.admitted_sender_messages.size ||
      state.sender_messages_being_delivered.size ||
      state.steer_settlements.size ||
      state.late_steers.size ||
      state.backend_event_pump ||
      state.backend_events.length ||
      state.child_is_quarantined ||
      state.settlement_tasks.size
    );
  }
  update_waiting_work(): readonly string[] {
    const background = this.options.store.read_live_background_task_ids();
    const metadata = this.update_metadata ?? this.options.store.update_work_metadata();
    return [
      ...new Set(
        [...this.conversations]
          .filter(
            ([identity, state]) => this.state_has_update_work(state) || background.has(identity),
          )
          .map(
            ([identity, state]) =>
              metadata.get(identity)?.[0] ?? `${state.record.backend_key} conversation`,
          ),
      ),
    ];
  }
  async prepare_update(mode: 'idle' | 'now', signal?: AbortSignal): Promise<boolean> {
    this.require_runtime_ownership();
    if (!this.update_admission_owned) {
      if (!this.workspace_admission.close_starts())
        throw new Error('Another operation owns workspace admission.');
      this.update_admission_owned = true;
      this.update_generation = Symbol('update');
      this.update_metadata = this.options.store.update_work_metadata();
      this.update_stop_failed = false;
    }
    const generation = this.update_generation!;
    if (this.update_retirement !== null && this.update_retirement_generation !== generation) {
      if (this.update_retirement_pending) return false;
      this.update_retirement = null;
    }
    if (this.update_stop_failed) {
      this.update_retirement = null;
      this.update_stop_failed = false;
    }
    await Promise.all(
      [...this.conversations.values()].map((state) => this.ensure_local_custody(state)),
    );
    const background = this.options.store.read_live_background_task_ids();
    const local = [...this.conversations]
      .filter(
        ([identity, state]) =>
          (this.update_metadata?.get(identity)?.[1] ?? HOME_MACHINE_ID) === HOME_MACHINE_ID &&
          state.retained_child_reference === null &&
          !state.child?.stop_outside_lock,
      )
      .map(([, state]) => state);
    if (this.update_retirement === null) {
      if (this.workspace_admission.active) return false;
      if (
        mode === 'idle' &&
        (this.options.store.blocked_local_conversations.size > 0 ||
          local.some(
            (state) =>
              this.state_has_update_work(state) || background.has(state.record.conversation_id),
          ))
      )
        return false;
      this.update_retirement_generation = generation;
      this.update_retirement_pending = true;
      this.update_retirement = (async () => {
        try {
          for (const state of local) {
            if (this.update_generation !== generation) return;
            if (
              state.child === null &&
              state.local_child_retirement_proven &&
              !this.state_has_update_work(state) &&
              !background.has(state.record.conversation_id)
            )
              continue;
            await this.retire_update_child(state, generation);
          }
          for (const state of local) {
            while (state.backend_event_pump) await state.backend_event_pump;
            await Promise.all([...state.settlement_tasks]);
          }
          await this.options.store.finish_writes();
        } catch (error) {
          if (this.update_generation === generation) this.update_stop_failed = true;
          throw error;
        } finally {
          this.update_retirement_pending = false;
        }
      })();
      void this.update_retirement.catch(() => {});
    }
    if (signal) {
      if (!(await observe_promise(this.update_retirement, signal)).completed) return false;
    } else await this.update_retirement;
    if (signal?.aborted || this.update_generation !== generation) return false;
    if (this.workspace_admission.closed) return true;
    return this.workspace_admission.claim(() => {}, {
      idle: () => local.every((state) => !this.state_has_update_work(state)),
      require_idle: true,
    });
  }
  private update_event_failed(state: ConversationState): boolean {
    return (
      state.backend_event_failures >
      (this.acknowledged_event_failures.get(state.record.conversation_id) ?? 0)
    );
  }
  private async retire_update_child(state: ConversationState, generation: symbol): Promise<void> {
    state.being_killed = true;
    try {
      const lease = await this.acquire_settled(state);
      let child: BackendChild | null;
      try {
        if (this.update_generation !== generation) return;
        await controls.discard_all(this, state);
        child = state.child;
        if (child) this.child_stop_ownership.set(child, state);
      } finally {
        lease.release();
      }
      if (child !== null) {
        await this.native_operation(() => child.stop());
        await child.settled?.();
        await Promise.all([...state.settlement_tasks]);
        while (state.backend_event_pump) await state.backend_event_pump;
        const event_failed = this.update_event_failed(state);
        await state.lock.run(async () => {
          await this._confirm_local_child_stopped(state, child);
          state.child_is_quarantined = false;
          if (state.child === child) {
            state.child = null;
            state.child_event_sink = null;
          }
          if (state.running_turn)
            await this.end_turn(state, state.running_turn, 'interrupted', null);
          set_phase(state, state.running_turn ? 'running' : 'idle');
        });
        if (event_failed) throw new Error('A backend result did not settle before the update.');
      }
      // A canceled attempt cannot discard messages admitted after Cancel.
      if (this.update_generation === generation) await this.kill(state.record.conversation_id);
      if (this.update_generation === generation && this.update_event_failed(state))
        throw new Error('A backend result did not settle before the update.');
    } catch (error) {
      if (state.child) state.child_is_quarantined = true;
      throw error;
    } finally {
      state.being_killed = false;
      if (this.update_generation !== generation) await this.drain_held_prompts(state);
    }
  }
  cancel_update(): boolean {
    this.workspace_admission.reopen();
    this.update_admission_owned = false;
    this.update_generation = null;
    this.update_stop_failed = false;
    this.update_metadata = null;
    this.acknowledged_event_failures = new Map(
      [...this.conversations].map(([identity, state]) => [identity, state.backend_event_failures]),
    );
    return true;
  }
  readonly retire_local_workspace = async (
    ticket_id: string,
    identity: WorkspaceIdentity,
    operation: () => MaybePromise<RetirementTarget>,
  ): Promise<RetirementTarget> => {
    if (this.retiring_workspace_tickets.has(ticket_id))
      return { reason: 'Ticket workspace retirement is already in progress' };
    this.retiring_workspace_tickets.add(ticket_id);
    let ids: readonly string[] = [];
    try {
      return await conversation_link_lock(`ticket:${ticket_id}`).run(async () =>
        this.workspace_admission.operation(async (admitted) => {
          if (!admitted) return { reason: 'Owner update in progress' };
          ids = await this.options.store.workspace_conversation_ids(ticket_id, identity);
          for (const id of ids) this.retiring_workspace_conversations.add(id);
          for (const id of ids) await this.kill(id);
          return await operation();
        }),
      );
    } catch (error) {
      console.error('Local workspace retirement failed', error);
      return { reason: 'Local child retirement failed' };
    } finally {
      for (const id of ids) this.retiring_workspace_conversations.delete(id);
      this.retiring_workspace_tickets.delete(ticket_id);
    }
  };
  async run_with_workspace_admission(
    operation: () => void,
    options: { require_idle: boolean },
  ): Promise<boolean> {
    return this.workspace_admission.claim(operation, {
      idle: () => this.workspace_activity() === 'idle',
      require_idle: options.require_idle,
    });
  }
  async wait_until_quiescent(): Promise<void> {
    while (true) {
      const tasks = [...this.conversations.values()].flatMap((state) => [
        ...state.settlement_tasks,
        ...(state.backend_event_pump ? [state.backend_event_pump] : []),
      ]);
      if (tasks.length === 0) return;
      await Promise.allSettled(tasks);
    }
  }
  settled(): Promise<void> {
    return this.shutdown_task ?? Promise.resolve();
  }
  shutdown(options: import('../lifecycle/contracts.ts').StopOptions): Promise<void> {
    if (!this.shutdown_task) this.begin_shutdown(options);
    return observe_shutdown(
      this.shutdown_task!,
      options,
      'Shutdown deadline expired; ownership retained: conversation core',
      ['conversation core'],
    );
  }
  private begin_shutdown(options: import('../lifecycle/contracts.ts').StopOptions): void {
    let resolve!: () => void, reject!: (error: unknown) => void;
    this.shutdown_task = new Promise<void>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    void this.shutdown_task.catch(() => {});
    this.shutting_down = true;
    if (this.deferred_start_timer !== null) clearTimeout(this.deferred_start_timer);
    this.deferred_start_timer = null;
    this.deferred_start_conversations.clear();
    this.workspace_admission.seal();
    const failures: unknown[] = [];
    try {
      this.options.on_shutdown?.();
    } catch (error) {
      failures.push(error);
    }
    const states = [...this.conversations.values()];
    const pumps = states.flatMap((state) =>
      state.backend_event_pump ? [state.backend_event_pump] : [],
    );
    // A child can already exist while its native start still awaits a response.
    for (const state of states) {
      if (state.child !== null) this.child_stop_ownership.set(state.child, state);
      if (state.preparing_child !== null)
        this.child_stop_ownership.set(state.preparing_child, state);
      if (state.child === null && state.preparing_child !== null)
        state.child = state.preparing_child;
      state.closing = true;
      state.child_event_sink = null;
      if (state.running_turn) state.running_turn.callbacks_revoked = true;
      state.backend_events.length = 0;
      state.backend_event_pump = null;
      state.late_steer_generation += 1;
      state.late_steers.clear();
      for (const barrier of state.steer_settlements) barrier.resolve();
      state.steer_settlements.clear();
      state.phase_settled.resolve();
      state.reserved_turn?.resolved.resolve();
    }
    for (const cancel of this.native_waiters) cancel();
    this.native_waiters.clear();
    this.shutdown_controller.abort();
    // Detach every child before waiting for a raw call. Exit can settle that call.
    const detaching = detach_children(
      states,
      [...this.child_stop_ownership].map(([child, state]) => ({ child, state })),
      options,
      async (state, child) => {
        if (child.stop_outside_lock) return;
        const generation = this.local_child_generations.get(child);
        if (generation !== undefined)
          await this.options.store.record_child_custody(
            state.record.conversation_id,
            generation,
            null,
            { retired: true },
          );
        state.local_child_retirement_proven = true;
      },
    ).then(() => {
      this.child_stop_ownership.clear();
    });
    const janitor = this.idle_janitor?.stop();
    void (async () => {
      const [cleanup, business] = await Promise.all([
        Promise.allSettled([detaching, janitor, ...this.native_settlements]),
        Promise.allSettled([
          ...pumps,
          ...(this.update_retirement ? [this.update_retirement] : []),
          ...states.flatMap((state) => [...state.settlement_tasks]),
        ]),
      ]);
      // Cancelling our own business observer is expected. A failed native detach,
      // janitor or store cleanup is never excused by its error name.
      for (const result of cleanup) if (result.status === 'rejected') failures.push(result.reason);
      for (const result of business) {
        if (
          result.status === 'rejected' &&
          !(this.shutting_down && is_shutdown_business_cancellation(result.reason))
        )
          failures.push(result.reason);
      }
      try {
        await this.options.store.finish_writes();
      } catch (error) {
        failures.push(error);
      }
      if (failures.length)
        throw new AggregateError(failures, 'Conversation core did not shut down cleanly');
    })().then(resolve, reject);
  }
}
