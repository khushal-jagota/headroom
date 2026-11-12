/** Turn and queue controls retain provider receipts before a successor takes ownership. */
import {
  BackendSteerAccepted,
  BackendSteerRefused,
  BackendSteerUncertain,
  PromptWriteFailed,
  PromptWriteRefused,
} from '../backends/contracts.ts';
import type { BackendChild, BackendSteerOutcome, TurnToken } from '../backends/contracts.ts';
import {
  backend_supports_steer,
  PromptDeliveryInjected,
  PromptDeliveryMode,
  PromptDeliveryQueued,
  PromptDeliveryRefused,
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
import type { SqliteProcessConversationSystem } from '../system.ts';
import type { Message, ReservedTurn } from './contracts.ts';
import { discarded_row, prompt_row, refused_row, uncertain_row } from './messages.ts';
import { ConversationState, Deferred, same_turn, set_phase } from './state.ts';
import type { LockLease } from './synchronization.ts';

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
      console.error(
        `conversation turn cancel failed conversation_id=${state.record.conversation_id} backend=${state.record.backend_key}`,
        error,
      );
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
): Promise<boolean> {
  state.last_touched_monotonic = host.monotonic_now();
  return state.lock.run(async () => {
    const index = state.held_prompts.findIndex((message) => message.held_prompt_id === identity);
    if (index < 0 || state.held_prompts[index].delivery_uncertain) return false;
    const [message] = state.held_prompts.splice(index, 1);
    host.publish_held_prompts_changed(state);
    const rows = await host.persist(state, () =>
      host.options.store.append_events(state.record.conversation_id, [discarded_row(message)], {
        recording_receipt: message.recording_receipt,
      }),
    );
    host.note_written(state, rows);
    return true;
  });
}
async function discard_all(
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
  }
}
export async function kill(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  state.being_killed = true;
  try {
    state.last_touched_monotonic = host.monotonic_now();
    // Local initial writes settle under their owner. Remote withdrawal belongs to Unit22.
    const lease = await host.acquire_settled(state);
    try {
      await discard_all(host, state);
      try {
        if (state.running_turn)
          await cancel_and_end_running_turn(host, state, state.running_turn, lease);
        if (state.child) await host.discard_child(state, state.child);
      } finally {
        if (!host.shutting_down) settle_phase(state);
      }
    } finally {
      lease.release();
    }
    await host.drain_held_prompts(state);
  } finally {
    try {
      if (!host.shutting_down)
        await host.persist(state, () =>
          host.options.store.take_background_tasks(state.record.conversation_id),
        );
    } finally {
      state.being_killed = false;
    }
  }
}
export async function send_now(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  message: Message,
): Promise<PromptDeliveryFate> {
  const lease = await host.acquire_settled(state);
  let reservation: ReservedTurn;
  let held: PromptDeliveryQueued | null = null;
  let automatic = false;
  try {
    try {
      if (
        state.child_is_quarantined ||
        state.held_prompts.some((entry) => entry.delivery_uncertain) ||
        state.running_turn?.automatic_compaction
      )
        return host.hold_prompt(state, message);
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
      automatic = host.automatic_compaction_is_due(state);
      if (automatic) held = host.hold_prompt(state, message);
      reservation = await host.reserve_turn(state, automatic);
      reservation.mode = automatic ? PromptDeliveryMode.queue : PromptDeliveryMode.send_now;
    } finally {
      lease.release();
    }
  } catch (error) {
    await host.drain_held_prompts(state);
    throw error;
  }
  if (automatic) {
    await host.deliver_automatic_compaction(state, reservation);
    return held!;
  }
  return host.deliver_and_finalize(state, reservation, [message], false);
}
interface SteerAttempt {
  readonly lifecycle: LifecycleFact;
  readonly outcome: BackendSteerOutcome | PromptDeliverySuppressed;
  readonly automatic_notice: boolean;
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
    return result;
  });
  if (decisions[0].suppression_reason !== null)
    return {
      lifecycle: attempt,
      outcome: new PromptDeliverySuppressed({ reason: decisions[0].suppression_reason }),
      automatic_notice: false,
      adapter_error: null,
    };
  let outcome: BackendSteerOutcome;
  let adapter_error: unknown = null;
  try {
    outcome = await host.native_operation(() =>
      child.steer(token, message.content, {
        sender_content: message.content,
        sender_label: message.sender_label,
      }),
    );
  } catch (error) {
    if (error instanceof PromptWriteRefused)
      outcome = new BackendSteerRefused({ refusal_reason: R.write_to_backend_failed });
    else {
      outcome = new BackendSteerUncertain();
      if (!(error instanceof PromptWriteFailed)) adapter_error = error;
    }
  }
  return {
    lifecycle: attempt,
    outcome,
    automatic_notice: decisions[0].is_manager_notice,
    adapter_error,
  };
}
function watch_late_steer(
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
          let position = 0;
          state.held_prompts.forEach((held, index) => {
            if (held_before.includes(held.held_prompt_id)) position = index + 1;
          });
          host.hold_prompt(
            state,
            { ...message, recording_receipt: null, queue_reason: PromptQueueReason.steer_refused },
            position,
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
  outcome: BackendSteerOutcome,
  token: TurnToken | null,
  attempt: LifecycleFact | null = null,
  automatic_notice = false,
  held_before: readonly string[] = [],
): Promise<PromptDeliveryFate> {
  const accepted = outcome instanceof BackendSteerAccepted;
  const refused = outcome instanceof BackendSteerRefused;
  const fate = accepted
    ? new PromptDeliveryInjected()
    : refused
      ? new PromptDeliveryRefused({ refusal_reason: outcome.refusal_reason })
      : new PromptDeliveryUncertain();
  const payload = accepted
    ? prompt_row(message, PromptDeliveryMode.steer)
    : refused
      ? refused_row(message, PromptDeliveryMode.steer, outcome.refusal_reason)
      : uncertain_row(message, PromptDeliveryMode.steer);
  const rows = await host.persist(state, () =>
    host.options.store.append_events(state.record.conversation_id, [payload], {
      lifecycle: attempt
        ? [{ ...attempt, status: accepted ? S.injected : refused ? S.refused : S.unknown }]
        : [],
      native_message_admission: { fate },
      recording_receipt: message.recording_receipt,
      owner_read_through_sequence:
        message.sender?.kind === 'owner' && (accepted || message.acknowledge_owner_on_failure)
          ? message.owner_read_through_sequence
          : null,
    }),
  );
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
  if (outcome instanceof BackendSteerUncertain && outcome.not_delivered !== null)
    watch_late_steer(host, state, outcome.not_delivered, message, held_before);
  if (accepted || !refused) {
    if (
      outcome.composed_content_delivered &&
      state.running_turn &&
      same_turn(state.running_turn.token, token)
    )
      state.running_turn.automatic_notice = state.running_turn.automatic_notice && automatic_notice;
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
    if (
      !state.child_is_quarantined &&
      state.phase === 'idle' &&
      state.held_prompts.length === 0 &&
      state.late_steers.size === 0
    )
      reservation = await host.reserve_turn(state);
    else if (message.content.some((piece) => !(piece instanceof MessageText)))
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
      !backend_supports_steer(state.record.backend_key)
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
    const fate = await state.lock.run(() =>
      record_steer_outcome(
        host,
        state,
        message,
        result.outcome as BackendSteerOutcome,
        token,
        result.lifecycle,
        result.automatic_notice,
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
  try {
    const index = state.held_prompts.findIndex((entry) => entry.held_prompt_id === identity);
    if (index < 0) return null;
    message = state.held_prompts[index];
    if (message.delivery_uncertain) return new PromptDeliveryUncertain();
    if (state.child_is_quarantined || state.held_prompts.some((entry) => entry.delivery_uncertain))
      return new PromptDeliveryQueued({ queue_position: index + 1 });
    if (mode === 'send_now') {
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
      reservation = await host.reserve_turn(state);
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
      const reason = !backend_supports_steer(state.record.backend_key)
        ? R.backend_cannot_steer
        : state.running_turn === null || state.child === null
          ? R.no_running_turn_to_steer_into
          : null;
      if (reason) {
        immediate = await record_steer_outcome(
          host,
          state,
          message,
          new BackendSteerRefused({ refusal_reason: reason }),
          null,
        );
        finish_held(state, message);
      } else {
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
    await host.drain_held_prompts(state);
    return immediate;
  }
  try {
    const result = await attempt_steer(host, state, token!, child!, message!);
    if (result.outcome instanceof PromptDeliverySuppressed) return result.outcome;
    if (
      result.outcome instanceof BackendSteerRefused &&
      (result.outcome.refusal_reason === R.command_cannot_join_running_turn ||
        result.outcome.refusal_reason === R.remote_steer_not_delivered)
    ) {
      await host.persist(state, () =>
        host.options.store.transition_lifecycle(state.record.conversation_id, [
          { ...result.lifecycle, status: S.refused },
        ]),
      );
      release_steer(state, barrier!);
      barrier = null;
      return state.lock.run(() => {
        finish_held(state, message!);
        return host.hold_prompt(state, {
          ...message!,
          queue_reason: queue_reason((result.outcome as BackendSteerRefused).refusal_reason),
        });
      });
    }
    const fate = await state.lock.run(() =>
      record_steer_outcome(
        host,
        state,
        message!,
        result.outcome as BackendSteerOutcome,
        token,
        result.lifecycle,
        result.automatic_notice,
        held_before,
      ),
    );
    if (result.adapter_error !== null) throw result.adapter_error;
    return fate;
  } finally {
    await state.lock.run(() => finish_held(state, message!));
    if (barrier) release_steer(state, barrier);
  }
}
