import { note_pending_owner_sends } from '../record/owner_send_receipts.ts';
/** Turn and queue controls retain provider receipts before a successor takes ownership. */
import {
  BackendSteerAccepted,
  BackendSteerRefused,
  BackendSteerUncertain,
  PromptWriteFailed,
  PromptWriteRefused,
} from '../backends/contracts.ts';
import type { BackendChild, BackendSteerOutcome, TurnToken } from '../backends/contracts.ts';
import { log_backend_failure } from '../backends/failure_summary.ts';
import {
  backend_supports_active_steering,
  PromptDeliveryInjected,
  PromptDeliveryMode,
  PromptDeliveryQueued,
  PromptDeliveryRefusalReason as R,
  PromptDeliveryStarted,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
  PromptQueueReason,
} from '../contracts.ts';
import type { HeldPromptPromotionMode, PromptDeliveryFate } from '../contracts.ts';
import { MessageText } from '../message_content.ts';
import type { LifecycleFact } from '../record/lifecycle.ts';
import { ConversationRecord } from '../record/contracts.ts';
import { LifecycleOperation as O, LifecycleStatus as S } from '../record/lifecycle.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import type { Message, ReservedTurn } from './contracts.ts';
import { discarded_row, prompt_row, uncertain_row } from './messages.ts';
import { Deferred } from '../../core/deferred.ts';
import { ConversationState, same_turn, set_phase } from './state.ts';
import type { LockLease } from './synchronization.ts';
import { child_factory_for, kill_initial_delivery, retained_factory } from './remote.ts';
import {
  PromptDeliveryUncertainEventPayload,
  PromptEventPayload,
  PromptDeliveryRefusedEventPayload,
  PromptDiscardedEventPayload,
} from '../events.ts';
import { isDeepStrictEqual } from 'node:util';

function settle_phase(state: ConversationState): void {
  set_phase(state, state.running_turn === null ? 'idle' : 'running');
}
function queue_reason(reason: R): PromptQueueReason {
  return reason === R.command_cannot_join_running_turn
    ? PromptQueueReason.command_needs_its_own_turn
    : PromptQueueReason.steer_refused;
}
function release_steer(state: ConversationState, barrier: Deferred<void>): void {
  state.steer_settlements.delete(barrier);
  barrier.resolve();
}
function begin_held(state: ConversationState, message: Message): void {
  if (message.sender_message_id !== null)
    state.sender_messages_being_delivered.set(message.sender_message_id, {
      message,
      settled: new Deferred<void>(),
    });
}
function finish_held(state: ConversationState, message: Message): void {
  if (message.sender_message_id === null) return;
  const admission = state.sender_messages_being_delivered.get(message.sender_message_id);
  state.sender_messages_being_delivered.delete(message.sender_message_id);
  admission?.settled.resolve();
}
function position_after(state: ConversationState, held_before: readonly string[]): number {
  let position = 0;
  state.held_prompts.forEach((held, index) => {
    if (held_before.includes(held.held_prompt_id)) position = index + 1;
  });
  return position;
}
// A refused promotion was not delivered. Hold it again where it was, as direct steer does.
function keep_refused_promotion(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  message: Message,
  held_before: readonly string[],
  refusal_reason: R,
): PromptDeliveryQueued {
  finish_held(state, message);
  return host.hold_prompt(
    state,
    { ...message, queue_reason: queue_reason(refusal_reason) },
    position_after(state, held_before),
  );
}
export async function cancel_and_end_running_turn(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  running: NonNullable<ConversationState['running_turn']>,
  lease: LockLease,
): Promise<void> {
  set_phase(state, 'ending');
  running.ending_is_the_cores = true;
  await host.revoke_turn_requests(state, running);
  const child = state.child;
  let cancel_fact: LifecycleFact | null = null;
  await lease.unlocked(async () => {
    if (!child) return;
    const attempt = await host.begin_attempt(state, running.token, O.cancel);
    try {
      await host.native_operation(() => child.cancel_running_turn());
      cancel_fact = { ...attempt, status: S.accepted };
    } catch (error) {
      if (host.shutting_down) throw error;
      log_backend_failure(state.record.conversation_id, state.record.backend_key, error);
      await host.discard_child(state, child);
      cancel_fact = { ...attempt, status: S.unknown };
    }
  });
  await host.end_turn(state, running, 'interrupted', null, cancel_fact);
}
export async function interrupt(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  state.last_touched_monotonic = host.monotonic_now();
  const lease = await host.acquire_settled(state);
  try {
    if (!state.running_turn) return;
    try {
      await cancel_and_end_running_turn(host, state, state.running_turn, lease);
    } finally {
      settle_phase(state);
    }
  } finally {
    lease.release();
  }
  await host.drain_held_prompts(state);
}
export async function discard_held_prompt(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  identity: string,
  withdrawn_for_edit = false,
): Promise<boolean> {
  state.last_touched_monotonic = host.monotonic_now();
  return state.lock.run(async () => {
    const index = state.held_prompts.findIndex((message) => message.held_prompt_id === identity);
    if (index < 0 || state.held_prompts[index].delivery_uncertain) return false;
    const [message] = state.held_prompts.splice(index, 1);
    host.publish_held_prompts_changed(state);
    const rows = await host.persist(state, () =>
      host.options.store.append_events(
        state.record.conversation_id,
        [discarded_row(message, null, withdrawn_for_edit)],
        { recording_receipt: message.recording_receipt },
      ),
    );
    host.note_written(state, rows);
    await host.settle_automatic_ids(state, [message.sender_message_id]);
    return true;
  });
}
export async function discard_all(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  state.late_steer_generation += 1;
  state.late_steers.clear();
  for (const message of [...state.held_prompts]) {
    if (message.delivery_uncertain) continue;
    state.held_prompts.splice(state.held_prompts.indexOf(message), 1);
    host.publish_held_prompts_changed(state);
    host.note_written(
      state,
      await host.persist(state, () =>
        host.options.store.append_events(state.record.conversation_id, [
          discarded_row({ ...message, recovery_references: [] }),
        ]),
      ),
    );
    await host.settle_automatic_ids(state, [message.sender_message_id]);
  }
}
export async function kill(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  restart?: Parameters<SqliteProcessConversationSystem['confirm_killed_source']>[1],
): Promise<void> {
  state.being_killed = true;
  let succeeded = false;
  let background_settled = false;
  try {
    await kill_owned(host, state);
    if (restart && !host.shutting_down) {
      await host.persist(state, () =>
        host.options.store.take_background_tasks(state.record.conversation_id),
      );
      background_settled = true;
    }
    await host.confirm_killed_source(state, restart);
    succeeded = true;
  } finally {
    try {
      if (!host.shutting_down && !background_settled)
        await host.persist(state, () =>
          host.options.store.take_background_tasks(state.record.conversation_id),
        );
    } catch (error) {
      succeeded = false;
      throw error;
    } finally {
      state.being_killed = false;
      if (
        !succeeded &&
        !state.restart_retired &&
        !state.child_is_quarantined &&
        !host.options.store.blocked_local_conversations.has(state.record.conversation_id)
      )
        host.schedule_drain(state);
    }
  }
  if (!restart) await host.drain_held_prompts(state);
}
export const SAVE_FAILURE_TURN_SUMMARY =
  'Panels could not save this conversation, so it ended this turn and stopped the agent. A server restart clears the failure.';
/** A save gave up: stop the child, then end the running turn as failed. Each save gets one attempt. */
export async function fail_after_save_failure(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  // The core owns this ending, so late child events for the turn are not taken in.
  if (state.running_turn) state.running_turn.ending_is_the_cores = true;
  // Stop outside the lock, so an unfinished delivery cannot hold the stop back.
  if (state.child) await host.discard_child(state, state.child);
  if (host.shutting_down) return;
  const lease = await host.acquire_after_steers(state);
  try {
    const running = state.running_turn;
    if (!running || running.ended || host.shutting_down) return;
    const was_running = state.phase === 'running';
    if (was_running) set_phase(state, 'ending');
    try {
      await host.end_turn(state, running, 'failed', SAVE_FAILURE_TURN_SUMMARY);
    } catch (error) {
      console.error(
        `conversation ${state.record.conversation_id} could not record its failed turn`,
        error,
      );
    } finally {
      if (was_running && !host.shutting_down) settle_phase(state);
    }
  } finally {
    lease.release();
  }
}
async function kill_owned(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  state.last_touched_monotonic = host.monotonic_now();
  if (await kill_initial_delivery(host, state, () => discard_all(host, state))) {
    return;
  }
  const lease = await host.acquire_settled(state);
  try {
    await discard_all(host, state);
    try {
      if (state.child === null && state.retained_child_reference !== null)
        state.child =
          retained_factory(child_factory_for(host, state.record.backend_key))?.recover(
            state.retained_child_reference,
          ) ?? null;
      if (state.running_turn)
        await cancel_and_end_running_turn(host, state, state.running_turn, lease);
      if (state.child) await host.discard_child(state, state.child);
    } finally {
      if (!host.shutting_down) settle_phase(state);
    }
  } finally {
    lease.release();
  }
  await state.retirement_task;
}
export async function send_now(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  message: Message,
): Promise<PromptDeliveryFate> {
  const lease = await host.acquire_settled(state);
  let reservation: ReservedTurn;
  try {
    try {
      if (state.restart_retired)
        throw new PlannerError('retry_later', 'This Worker conversation was restarted');
      if (
        state.being_killed ||
        state.child_is_quarantined ||
        state.held_prompts.some((entry) => entry.delivery_uncertain) ||
        state.running_turn?.automatic_compaction
      )
        return host.hold_prompt(state, message);
      if (!host.start_admission.begin_queued([message.sender_message_id])) {
        const fate = host.hold_prompt(state, message);
        host.defer_start_drain(state);
        return fate;
      }
      if (state.running_turn) {
        try {
          await cancel_and_end_running_turn(host, state, state.running_turn, lease);
        } catch (error) {
          settle_phase(state);
          throw error;
        }
      }
      if (state.child_is_quarantined) {
        settle_phase(state);
        return host.hold_prompt(state, message);
      }
      reservation = await host.reserve_turn(state, false, [message.sender_message_id]);
      reservation.mode = PromptDeliveryMode.send_now;
    } finally {
      lease.release();
    }
  } catch (error) {
    await host.drain_held_prompts(state);
    throw error;
  }
  return host.deliver_and_finalize(state, reservation, [message], false);
}
interface SteerAttempt {
  readonly lifecycle: LifecycleFact;
  readonly outcome: BackendSteerOutcome | PromptDeliverySuppressed;
  readonly automatic_notice: boolean;
  readonly memory_notice_only: boolean;
  readonly adapter_error: unknown;
}
async function attempt_steer(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  token: TurnToken,
  child: BackendChild,
  message: Message,
): Promise<SteerAttempt> {
  const attempt = await host.begin_attempt(state, token, O.steer);
  const decisions = await state.lock.run(async () => {
    const result = await host.persist(state, () =>
      host.options.store.admit_sender_messages(
        state.record.conversation_id,
        [discarded_row(message)],
        { lifecycle_if_all_suppressed: [{ ...attempt, status: S.suppressed }] },
      ),
    );
    host.note_written(
      state,
      host.options.store.read_events_after(
        state.record.conversation_id,
        state.record.latest_sequence,
      ),
    );
    if (result[0].suppression_reason !== null)
      await host.settle_automatic_ids(state, [message.sender_message_id]);
    return result;
  });
  if (decisions[0].suppression_reason !== null)
    return {
      lifecycle: attempt,
      outcome: new PromptDeliverySuppressed({ reason: decisions[0].suppression_reason }),
      automatic_notice: false,
      memory_notice_only: false,
      adapter_error: null,
    };
  let outcome: BackendSteerOutcome;
  let adapter_error: unknown = null;
  host.start_admission.queue(message.sender_message_id);
  host.start_admission.bind([message.sender_message_id], token);
  await host.persist(state, () =>
    host.start_admission.persist_bindings(host.options.store.start_admission_database, token),
  );
  try {
    await host.persist(state, () =>
      note_pending_owner_sends(
        host.options.store.start_admission_database,
        state.record.conversation_id,
        [message],
        PromptDeliveryMode.steer,
        true,
      ),
    );
    host.start_admission.bind_source(token, child);
    outcome = await host.native_operation(() =>
      child.steer(token, message.content, {
        sender_content: message.content,
        sender_label: message.sender_label,
      }),
    );
    // A refused steer goes back to the queue unrecorded, so its backend text is logged here.
    if (outcome instanceof BackendSteerRefused && outcome.refusal_detail !== null)
      log_backend_failure(
        state.record.conversation_id,
        state.record.backend_key,
        outcome,
        outcome.refusal_detail,
      );
  } catch (error) {
    if (error instanceof PromptWriteRefused || error instanceof PromptWriteFailed)
      log_backend_failure(state.record.conversation_id, state.record.backend_key, error);
    if (error instanceof PromptWriteRefused)
      outcome = new BackendSteerRefused({
        refusal_reason: R.write_to_backend_failed,
        refusal_detail: error.message,
      });
    else {
      outcome = new BackendSteerUncertain();
      if (!(error instanceof PromptWriteFailed)) adapter_error = error;
    }
  }
  return {
    lifecycle: attempt,
    outcome,
    automatic_notice: decisions[0].is_manager_notice,
    memory_notice_only: decisions[0].is_memory_notice,
    adapter_error,
  };
}
export function watch_late_steer(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  not_delivered: Promise<boolean>,
  message: Message,
  held_before: readonly string[],
): void {
  const generation = state.late_steer_generation;
  let task!: Promise<void>;
  task = (async () => {
    let never_arrived = false;
    try {
      never_arrived = await not_delivered;
    } catch (error) {
      console.error(
        `conversation ${state.record.conversation_id}: a late steer's outcome could not be read`,
        error,
      );
    }
    if (host.shutting_down || generation !== state.late_steer_generation) return;
    if (never_arrived) {
      await state.lock.run(() => {
        if (host.shutting_down || generation !== state.late_steer_generation) return;
        if (
          message.sender_message_id === null ||
          !state.held_prompts.some((held) => held.sender_message_id === message.sender_message_id)
        ) {
          host.hold_prompt(
            state,
            { ...message, recording_receipt: null, queue_reason: PromptQueueReason.steer_refused },
            position_after(state, held_before),
          );
        }
      });
    }
    state.late_steers.delete(task);
    if (!host.shutting_down && generation === state.late_steer_generation)
      await host.drain_held_prompts(state);
  })();
  state.late_steers.add(task);
  void task.finally(() => state.late_steers.delete(task)).catch(() => {});
}
async function record_steer_outcome(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  message: Message,
  outcome: BackendSteerAccepted | BackendSteerUncertain,
  token: TurnToken,
  attempt: LifecycleFact,
  automatic_notice: boolean,
  memory_notice_only: boolean,
  held_before: readonly string[],
): Promise<PromptDeliveryFate> {
  const accepted = outcome instanceof BackendSteerAccepted;
  const fate = accepted ? new PromptDeliveryInjected() : new PromptDeliveryUncertain();
  const payload = accepted
    ? prompt_row(message, PromptDeliveryMode.steer)
    : uncertain_row(message, PromptDeliveryMode.steer);
  let rows: Awaited<ReturnType<typeof host.options.store.append_events>>;
  try {
    rows = await host.persist(state, () =>
      host.options.store.append_events(state.record.conversation_id, [payload], {
        lifecycle: [{ ...attempt, status: accepted ? S.injected : S.unknown }],
        native_message_admission: { fate },
        recording_receipt: message.recording_receipt,
        owner_read_through_sequence:
          message.sender?.kind === 'owner' && (accepted || message.acknowledge_owner_on_failure)
            ? message.owner_read_through_sequence
            : null,
      }),
    );
  } catch (error) {
    // The agent can have this steer. A save that gave up must not tell the sender it was not sent.
    if (error instanceof Error && error.name === 'AbortError') throw error;
    console.error(`conversation ${state.record.conversation_id} could not record a steer`, error);
    return new PromptDeliveryUncertain();
  }
  host.note_written(state, rows);
  const boundary =
    message.sender?.kind === 'owner' && (accepted || message.acknowledge_owner_on_failure)
      ? message.owner_read_through_sequence
      : null;
  if (boundary !== null)
    state.record = new ConversationRecord({
      ...state.record,
      owner_read_through_sequence: Math.max(state.record.owner_read_through_sequence, boundary),
    });
  try {
    if (accepted) {
      await host.settle_automatic_ids(state, [message.sender_message_id]);
      await host.accept_automatic_start(state, token);
    }
  } catch (error) {
    // The outcome is recorded. A later save that gave up must not change what the sender hears.
    if ((error instanceof Error && error.name === 'AbortError') || !host.save_gave_up(state))
      throw error;
    console.error(`conversation ${state.record.conversation_id} could not settle a steer`, error);
  }
  if (outcome instanceof BackendSteerUncertain && outcome.not_delivered !== null)
    watch_late_steer(host, state, outcome.not_delivered, message, held_before);
  if (
    outcome.composed_content_delivered &&
    state.running_turn &&
    same_turn(state.running_turn.token, token)
  ) {
    state.running_turn.automatic_notice = state.running_turn.automatic_notice && automatic_notice;
    state.running_turn.memory_notice_only =
      state.running_turn.memory_notice_only && memory_notice_only;
  }
  return fate;
}
export async function steer(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  message: Message,
): Promise<PromptDeliveryFate> {
  const lease = await host.acquire_settled(state);
  let reservation: ReservedTurn | null = null;
  let queued: PromptDeliveryQueued | null = null;
  let child: BackendChild | null = null;
  let token: TurnToken | null = null;
  let barrier: Deferred<void> | null = null;
  let held_before: readonly string[] = [];
  try {
    if (state.restart_retired)
      throw new PlannerError('retry_later', 'This Worker conversation was restarted');
    if (state.being_killed) queued = host.hold_prompt(state, message);
    else if (
      !state.child_is_quarantined &&
      state.phase === 'idle' &&
      state.held_prompts.length === 0 &&
      state.late_steers.size === 0
    ) {
      if (host.start_admission.begin_queued([message.sender_message_id]))
        reservation = await host.reserve_turn(state, false, [message.sender_message_id]);
      else queued = host.hold_prompt(state, message);
    } else if (message.content.some((piece) => !(piece instanceof MessageText)))
      queued = host.hold_prompt(state, { ...message, queue_reason: PromptQueueReason.attachment });
    else if (
      message.model_change !== null ||
      message.reasoning_effort_change !== null ||
      message.reasoning_effort_is_set
    )
      queued = host.hold_prompt(state, { ...message, queue_reason: PromptQueueReason.run_change });
    else if (
      state.child_is_quarantined ||
      state.held_prompts.some((entry) => entry.delivery_uncertain) ||
      state.running_turn === null ||
      state.child === null ||
      !backend_supports_active_steering(host.options.backend_registry, state.record.backend_key)
    )
      queued = host.hold_prompt(state, {
        ...message,
        queue_reason: PromptQueueReason.steer_refused,
      });
    if (!reservation && !queued) {
      child = state.child;
      token = state.running_turn!.token;
      held_before = state.held_prompts.map((held) => held.held_prompt_id);
      await host.persist(state, () =>
        host.options.store.all_sender_messages_are_manager_notices(state.record.conversation_id, [
          message.sender_message_id,
        ]),
      );
      barrier = new Deferred<void>();
      state.steer_settlements.add(barrier);
    }
  } finally {
    lease.release();
  }
  if (reservation) return host.deliver_and_finalize(state, reservation, [message], false);
  if (queued) return queued;
  try {
    const result = await attempt_steer(host, state, token!, child!, message);
    if (result.outcome instanceof PromptDeliverySuppressed) return result.outcome;
    if (result.outcome instanceof BackendSteerRefused) {
      await host.persist(state, () =>
        host.options.store.transition_lifecycle(state.record.conversation_id, [
          { ...result.lifecycle, status: S.refused },
        ]),
      );
      release_steer(state, barrier!);
      barrier = null;
      return host.queue(state, {
        ...message,
        queue_reason: queue_reason(result.outcome.refusal_reason),
      });
    }
    const outcome = result.outcome;
    const fate = await state.lock.run(() =>
      record_steer_outcome(
        host,
        state,
        message,
        outcome,
        token!,
        result.lifecycle,
        result.automatic_notice,
        result.memory_notice_only,
        held_before,
      ),
    );
    if (result.adapter_error !== null) throw result.adapter_error;
    return fate;
  } finally {
    if (barrier) release_steer(state, barrier);
  }
}
export async function promote_held_prompt(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  identity: string,
  mode: HeldPromptPromotionMode,
): Promise<PromptDeliveryFate | null> {
  const lease = await host.acquire_settled(state);
  let message: Message | null = null;
  let reservation: ReservedTurn | null = null;
  let child: BackendChild | null = null;
  let token: TurnToken | null = null;
  let barrier: Deferred<void> | null = null;
  let immediate: PromptDeliveryFate | null = null;
  let held_before: readonly string[] = [];
  let owns_delivery = true;
  try {
    const index = state.held_prompts.findIndex((entry) => entry.held_prompt_id === identity);
    if (index < 0) return null;
    message = state.held_prompts[index];
    if (message.delivery_uncertain) return new PromptDeliveryUncertain();
    if (state.child_is_quarantined || state.held_prompts.some((entry) => entry.delivery_uncertain))
      return new PromptDeliveryQueued({ queue_position: index + 1 });
    if (mode === 'send_now') {
      if (!host.start_admission.begin_queued([message.sender_message_id])) {
        host.defer_start_drain(state);
        return new PromptDeliveryQueued({ queue_position: index + 1 });
      }
      if (state.running_turn?.automatic_compaction)
        return new PromptDeliveryQueued({ queue_position: index + 1 });
      if (state.running_turn) {
        try {
          await cancel_and_end_running_turn(host, state, state.running_turn, lease);
        } catch (error) {
          settle_phase(state);
          throw error;
        }
      }
      if (state.child_is_quarantined) {
        settle_phase(state);
        return new PromptDeliveryQueued({ queue_position: index + 1 });
      }
      begin_held(state, message);
      state.held_prompts.splice(index, 1);
      host.publish_held_prompts_changed(state);
      try {
        reservation = await host.reserve_turn(state, false, [message.sender_message_id]);
      } catch (error) {
        // The message was not sent. Hold it again, so it is neither lost nor left in delivery.
        finish_held(state, message);
        state.held_prompts.splice(index, 0, message);
        host.publish_held_prompts_changed(state);
        throw error;
      }
      reservation.mode = PromptDeliveryMode.send_now;
    } else {
      if (
        message.model_change !== null ||
        message.reasoning_effort_change !== null ||
        message.reasoning_effort_is_set
      )
        return new PromptDeliveryQueued({ queue_position: index + 1 });
      begin_held(state, message);
      held_before = state.held_prompts.slice(0, index).map((held) => held.held_prompt_id);
      state.held_prompts.splice(index, 1);
      host.publish_held_prompts_changed(state);
      const reason = !backend_supports_active_steering(
        host.options.backend_registry,
        state.record.backend_key,
      )
        ? R.backend_cannot_steer
        : state.running_turn === null || state.child === null
          ? R.no_running_turn_to_steer_into
          : null;
      if (reason) immediate = keep_refused_promotion(host, state, message, held_before, reason);
      else {
        child = state.child;
        token = state.running_turn!.token;
        await host.persist(state, () =>
          host.options.store.all_sender_messages_are_manager_notices(state.record.conversation_id, [
            message!.sender_message_id,
          ]),
        );
        barrier = new Deferred<void>();
        state.steer_settlements.add(barrier);
      }
    }
  } finally {
    lease.release();
  }
  if (reservation) {
    try {
      return await host.deliver_and_finalize(state, reservation, [message!], true);
    } finally {
      await state.lock.run(() => finish_held(state, message!));
    }
  }
  if (immediate) {
    // The next turn can wait on a remote result. This call must not wait for it.
    host.schedule_drain(state);
    return immediate;
  }
  try {
    const result = await attempt_steer(host, state, token!, child!, message!);
    if (result.outcome instanceof PromptDeliverySuppressed) return result.outcome;
    if (result.outcome instanceof BackendSteerRefused) {
      const { refusal_reason } = result.outcome;
      await host.persist(state, () =>
        host.options.store.transition_lifecycle(state.record.conversation_id, [
          { ...result.lifecycle, status: S.refused },
        ]),
      );
      release_steer(state, barrier!);
      barrier = null;
      const queued = await state.lock.run(() => {
        owns_delivery = false;
        return keep_refused_promotion(host, state, message!, held_before, refusal_reason);
      });
      host.schedule_drain(state);
      return queued;
    }
    const outcome = result.outcome;
    const fate = await state.lock.run(() =>
      record_steer_outcome(
        host,
        state,
        message!,
        outcome,
        token!,
        result.lifecycle,
        result.automatic_notice,
        result.memory_notice_only,
        held_before,
      ),
    );
    if (result.adapter_error !== null) throw result.adapter_error;
    return fate;
  } finally {
    // A message held again belongs to the queue. A later promotion can own its delivery now.
    if (owns_delivery) await state.lock.run(() => finish_held(state, message!));
    if (barrier) release_steer(state, barrier);
  }
}

export async function await_late_steer(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
  sequence: number,
  not_delivered: Promise<boolean>,
): Promise<void> {
  host.require_runtime_ownership();
  const state = host.conversation_state(conversation_id);
  if (state === null) return;
  const row = host.options.store.read_event(conversation_id, sequence);
  if (
    !(row?.payload instanceof PromptDeliveryUncertainEventPayload) ||
    row.payload.mode !== PromptDeliveryMode.steer
  )
    return;
  const payload = row.payload;
  const later = host.options.store.read_events_after(conversation_id, sequence);
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
  watch_late_steer(
    host,
    state,
    not_delivered,
    {
      requested_mode: payload.mode,
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
