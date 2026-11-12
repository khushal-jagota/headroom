/** Local process core. Registered as ConversationSystem after the turn-control slice lands. */
import { randomUUID } from 'node:crypto';
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
} from './events.ts';
import type { ConversationLiveTailFrame } from './events.ts';
import { interpret_backend_event } from './logic/backend_events.ts';
import { resolve_conversation_start_request } from './logic/conversation_start_resolution.ts';
import { leading_run_that_can_share_a_turn } from './logic/held_line.ts';
import { require_message_content } from './message_content.ts';
import type { MessageContent } from './message_content.ts';
import { ConversationRecord } from './record/contracts.ts';
import type { RunningTurn, StoredConversationEvent } from './record/contracts.ts';
import {
  LifecycleKind as K,
  LifecycleOperation as O,
  LifecycleStatus as S,
} from './record/lifecycle.ts';
import type { LifecycleFact } from './record/lifecycle.ts';
import type {
  ConversationCoreOptions,
  DeliveryAttempt,
  DeliveryServices,
  Message,
  ReservedTurn,
  RuntimeMutation,
} from './runtime/contracts.ts';
import { deliver_prompt, stop_child } from './runtime/delivery.ts';
import { MODEL_THINKING_PULSE_INTERVAL_SECONDS } from './runtime/configuration.ts';
import { take_backend_event } from './runtime/backend_events.ts';
import * as controls from './runtime/controls.ts';
import * as answers from './runtime/answers.ts';
import { acquire_after_steers, acquire_settled } from './runtime/synchronization.ts';
import type { LockLease } from './runtime/synchronization.ts';
import {
  automatic_compaction_is_due,
  deliver_automatic_compaction,
  background_tasks_ended_with_child,
  IdleChildJanitor,
  detach_children,
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
  readonly conversations = new Map<string, ConversationState>();
  shutting_down = false;
  private shutdown_task: Promise<void> | null = null;
  private idle_janitor: IdleChildJanitor | null = null;
  constructor(options: ConversationCoreOptions) {
    for (const key of Object.values(ConversationBackendKey)) {
      if (!options.backend_child_factories[key])
        throw new Error(`no backend child factory for ${key}`);
    }
    this.options = options;
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
    this.conversations.set(conversation_id, state);
    return state;
  }
  runtime_turn(conversation_id: string): RunningTurn | null {
    return this.conversations.get(conversation_id)?.running_turn ?? null;
  }
  async start_conversation(request: ConversationStartRequest): Promise<void> {
    return this.workspace_admission.operation(async (admitted) => {
      if (!admitted) throw new Error('Workspace admission is closed for an owner update.');
      this.require_runtime_ownership();
      // The record is the first effect, before an in-memory identity can exist.
      const record = this.options.store.create_conversation(
        resolve_conversation_start_request(request),
      );
      if (!this.conversations.has(record.conversation_id)) {
        this.conversations.set(record.conversation_id, new ConversationState(record));
      }
    });
  }
  persist<T>(
    state: ConversationState,
    work: () => T,
    mutations: readonly RuntimeMutation[] = [],
  ): Promise<T> {
    let failures = 0;
    let tracked: import('./runtime/contracts.ts').TrackedSaveRetry | null = null;
    const task = this.options.store.settle_recording(state.record.conversation_id, () => {
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
            tracked = this.options.track_save_retry?.(state.record.conversation_id, detail) ?? null;
        }
        throw error;
      }
    });
    return task.finally(() => tracked?.close());
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
    return this.workspace_admission.operation(async (admitted) => {
      if (!admitted) throw new Error('Workspace admission is closed for an owner update.');
      if (options.record_refusal && options.sender_message_id == null)
        throw new Error('record_refusal requires a sender_message_id');
      const state = this.conversation_state(conversation_id);
      if (!state) return new PromptDeliveryRefused({ refusal_reason: R.no_such_conversation });
      return own_settlement(
        state,
        this.send_owned(state, require_message_content(content), options),
      );
    });
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
    if (duplicate) return duplicate;
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
      return fate;
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
        state.child_is_quarantined ||
        state.phase !== 'idle' ||
        state.held_prompts.length > 0 ||
        state.late_steers.size > 0
      )
        return this.hold_prompt(state, message);
      if (this.automatic_compaction_is_due(state)) {
        const fate = this.hold_prompt(state, message);
        return { automatic: await this.reserve_turn(state, true), fate };
      }
      return this.reserve_turn(state);
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
    state.held_prompts.splice(position, 0, held);
    this.publish_held_prompts_changed(state);
    return new PromptDeliveryQueued({ queue_position: position + 1 });
  }
  async reserve_turn(
    state: ConversationState,
    automatic_compaction = false,
  ): Promise<ReservedTurn> {
    const number = await this.persist(state, () =>
      this.options.store.reserve_turn(state.record.conversation_id),
    );
    const reserved: ReservedTurn = {
      token: { conversation_id: state.record.conversation_id, turn_number: number },
      resolved: new Deferred<void>(),
      automatic_compaction,
      automatic_notice: false,
      prompt_accepted: false,
      messages: [],
      mode: PromptDeliveryMode.queue,
    };
    state.model_thinking_shown_at_monotonic = 0;
    state.reserved_turn = reserved;
    set_phase(state, 'starting');
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
    const promoted = queued && reservation.mode === PromptDeliveryMode.send_now;
    let delivery: DeliveryAttempt;
    try {
      reservation.automatic_notice = await this.persist(state, () =>
        this.options.store.all_sender_messages_are_manager_notices(
          state.record.conversation_id,
          batch.map((message) => message.sender_message_id),
        ),
      );
      delivery = await deliver_prompt(this, state, reservation, batch);
    } catch (error) {
      if (error instanceof PromptWriteFailed) {
        await this.record_delivery_uncertainty(state, reservation, batch);
        return new PromptDeliveryUncertain();
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
      await state.lock.run(() => {
        this.abandon_reservation(state, reservation, queued && !promoted ? 'draining' : 'idle');
        this.finish_held_deliveries(state, batch);
      });
      if (!queued || promoted) this.schedule_drain(state);
      return new PromptDeliverySuppressed({ reason: delivery.suppression_reason });
    }
    reservation.automatic_notice = delivery.automatic_notice;
    const survivors = delivery.messages.length > 0 ? delivery.messages : batch;
    let held_change: ModelChangedEventPayload | null = null;
    const fate = await state.lock.run(async () => {
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
                    owner_read_through_sequence: message.acknowledge_owner_on_failure
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
        if (change && queued) held_change = change;
        this.options.emit_change?.();
        return new PromptDeliveryStarted();
      } finally {
        if (state.phase !== 'starting') {
          state.reserved_turn = null;
          reservation.resolved.resolve();
        }
      }
    });
    await state.lock.run(() => this.finish_held_deliveries(state, batch));
    if (held_change !== null) {
      const committed_change = held_change as ModelChangedEventPayload;
      await this.options.on_held_model_change?.(
        state.record.conversation_id,
        committed_change.model,
        committed_change.reasoning_effort,
      );
    }
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
  schedule_drain(state: ConversationState): void {
    if (this.shutting_down) return;
    void own_settlement(state, this.drain_held_prompts(state)).catch((error) =>
      console.error(
        `conversation ${state.record.conversation_id} could not drain held prompts`,
        error,
      ),
    );
  }
  async drain_held_prompts(state: ConversationState): Promise<void> {
    const taken = await state.lock.run(() => {
      if (
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
    if (!taken) return;
    while (true) {
      const next = await state.lock.run(async () => {
        if (state.child_is_quarantined || state.held_prompts.length === 0) {
          set_phase(state, 'idle');
          return null;
        }
        const batch = leading_run_that_can_share_a_turn(state.held_prompts);
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
        return { batch, reservation: await this.reserve_turn(state) };
      });
      if (!next) return;
      const fate = await this.deliver_and_finalize(state, next.reservation, next.batch, true);
      if (fate instanceof PromptDeliveryStarted || fate instanceof PromptDeliveryUncertain) return;
    }
  }
  make_sink(state: ConversationState): BackendEventSink {
    const sink: BackendEventSink = {
      report: async (token, event) => {
        if (this.shutting_down || state.child_event_sink !== sink) return;
        if (event instanceof AgentMessageCompleted) return;
        if (
          event instanceof AgentMessageDelta ||
          event instanceof ModelThinkingHappened ||
          event instanceof ToolCallProgress
        ) {
          if (token === null) return;
          const show = async () => {
            await this.wait_for_reservation(state, token);
            if (state.child_event_sink === sink) this.publish_live(state, token, event);
          };
          if (state.reserved_turn) this.enqueue_backend_event(state, show);
          else this.publish_live(state, token, event);
          return;
        }
        this.enqueue_backend_event(state, async () => {
          await this.take_backend_event(state, token, event, sink);
        });
      },
    };
    return sink;
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
  async wait_for_reservation(state: ConversationState, token: TurnToken | null): Promise<void> {
    while (state.reserved_turn && same_turn(state.reserved_turn.token, token)) {
      await state.reserved_turn.resolved.promise;
    }
  }
  async take_backend_event(
    state: ConversationState,
    token: TurnToken | null,
    event: BackendEventValue,
    expected_sink?: BackendEventSink,
  ): Promise<boolean> {
    return take_backend_event(this, state, token, event, expected_sink);
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
          agent_activity: !running.automatic_compaction,
          automatic_compaction_confirmed: running.compaction_confirmed,
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
      const latest_agent_activity_sequence = running.automatic_compaction
        ? state.record.latest_agent_activity_sequence
        : last.sequence;
      state.record = new ConversationRecord({
        ...state.record,
        latest_agent_activity_at: running.automatic_compaction
          ? state.record.latest_agent_activity_at
          : last.created_at,
        latest_agent_activity_sequence,
        automatically_compacted_through_sequence: running.compaction_confirmed
          ? latest_agent_activity_sequence
          : state.record.automatically_compacted_through_sequence,
        automatic_compaction_attempted_through_sequence:
          running.compaction_confirmed || interpreted.compaction_result !== null
            ? latest_agent_activity_sequence
            : state.record.automatic_compaction_attempted_through_sequence,
      });
    }
    this.options.emit_change?.();
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
    if (state.child === child) state.child = null;
    const stopped = await stop_child(state, child);
    if (stopped) {
      state.local_child_retirement_proven = true;
      state.child_event_sink = null;
      state.child_is_quarantined = false;
      await background_tasks_ended_with_child(this, state);
    } else {
      state.child = child;
      state.child_is_quarantined = true;
    }
    return stopped;
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
    if (state) await own_settlement(state, controls.kill(this, state));
  }
  async promote_held_prompt(
    conversation_id: string,
    held_prompt_id: string,
    mode: HeldPromptPromotionMode,
  ): Promise<PromptDeliveryFate | null> {
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
  async retirement_is_confirmed(conversation_id: string): Promise<boolean> {
    const state = this.conversation_state(conversation_id);
    return state
      ? state.lock.run(
          () =>
            state.child === null &&
            !state.child_is_quarantined &&
            (state.local_child_retirement_proven || !state.has_delivered_prompt),
        )
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
    return new Set(
      this.conversation_state(conversation_id)?.running_turn?.pending_permission_ask_ids ?? [],
    );
  }
  async pending_user_input_request_ids(conversation_id: string): Promise<ReadonlySet<string>> {
    return new Set(
      this.conversation_state(conversation_id)?.running_turn?.pending_user_input_questions.keys() ??
        [],
    );
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
      return state.lock.run(async () => {
        const result = await this.persist(state, () =>
          this.options.store.transact_events(conversation_id, operation),
        );
        this.note_written(state, result.written);
        return result.value;
      });
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
  async is_running(conversation_id: string): Promise<boolean> {
    this.require_runtime_ownership();
    return this.conversations.get(conversation_id)?.running_turn != null;
  }
  async has_pending_permission_ask(conversation_id: string): Promise<boolean> {
    this.require_runtime_ownership();
    return (
      (this.conversations.get(conversation_id)?.running_turn?.pending_permission_ask_ids.size ??
        0) > 0
    );
  }
  async has_pending_user_input(conversation_id: string): Promise<boolean> {
    this.require_runtime_ownership();
    return (
      (this.conversations.get(conversation_id)?.running_turn?.pending_user_input_questions.size ??
        0) > 0
    );
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
    if (this.shutting_down || this.workspace_admission.closed) return 'unknown';
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
  async shutdown(): Promise<void> {
    if (this.shutdown_task) return this.shutdown_task;
    this.shutting_down = true;
    this.shutdown_task = (async () => {
      for (const state of this.conversations.values()) {
        if (state.running_turn) state.running_turn.callbacks_revoked = true;
        state.backend_events.length = 0;
      }
      // Caller departure never cancels an accepted exchange. Keep the record door open.
      await Promise.allSettled(
        [...this.conversations.values()].flatMap((state) => [...state.settlement_tasks]),
      );
      await Promise.allSettled(
        [...this.conversations.values()].map(async (state) => {
          if (state.child) await stop_child(state, state.child);
        }),
      );
      await this.options.store.finish_writes();
    })();
    return this.shutdown_task;
  }
}
