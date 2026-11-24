import { note_pending_owner_sends } from '../record/owner_send_receipts.ts';
/** Sends, held prompts, and the drain that delivers them. */
import { ConversationState, own_settlement, set_phase } from './state.ts';
import { HeldPromptsChangedFrame, MessageToOwnerEventPayload } from '../events.ts';
import type { MessageContent } from '../message_content.ts';
import type { SendOptions, PromptDeliveryFate, RecordMessageToOwnerOptions } from '../contracts.ts';
import {
  PromptDeliveryRefused,
  PromptDeliveryRefusalReason as R,
  PromptDeliveryMode,
  PromptQueueReason,
  PromptDeliveryQueued,
  PromptDeliverySuppressed,
  PromptDeliveryInjected,
  PromptDeliveryUncertain,
  PromptDeliveryStarted,
} from '../contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import { require_message_content } from '../message_content.ts';
import type { Message } from './contracts.ts';
import * as controls from './controls.ts';
import { refused_row, same_message, outcome_fate } from './messages.ts';
import { ConversationRecord } from '../record/contracts.ts';
import { Deferred } from '../../core/deferred.ts';
import { randomUUID } from 'node:crypto';
import { leading_run_that_can_share_a_turn } from '../logic/held_line.ts';
import { contention_only_write } from './synchronization.ts';
import { isDeepStrictEqual } from 'node:util';
import type { SqliteProcessConversationSystem } from '../system.ts';

function workspace_is_retiring(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): boolean {
  return (
    host.retiring_workspace_conversations.has(state.record.conversation_id) ||
    host.retiring_workspace_tickets.has(
      new Map(state.record.identity_environment_variables).get('PLAN_TICKET_ID') ?? '',
    )
  );
}

export function publish_held_prompts_changed(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): void {
  host.options.publish_frame?.(state.record.conversation_id, new HeldPromptsChangedFrame());
}

export async function send(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
  content: MessageContent,
  options: SendOptions,
): Promise<PromptDeliveryFate> {
  return host.workspace_admission.operation(
    async (admitted) => {
      if (!admitted) throw new Error('Workspace admission is closed for an owner update.');
      if (options.record_refusal && options.sender_message_id == null)
        throw new Error('record_refusal requires a sender_message_id');
      host.require_runtime_ownership();
      const refusal = host.execution_refusal(conversation_id);
      if (refusal !== null) throw refusal;
      const state = host.conversation_state(conversation_id);
      if (!state) return new PromptDeliveryRefused({ refusal_reason: R.no_such_conversation });
      await host.ensure_local_custody(state);
      if (state.restart_retired)
        throw new PlannerError('retry_later', 'This Worker conversation was restarted');
      if (workspace_is_retiring(host, state))
        throw new Error('Ticket workspace admission is closed for retirement.');
      return own_settlement(
        state,
        send_owned(host, state, require_message_content(content), options),
      );
    },
    { starts: true },
  );
}

async function send_owned(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  content: MessageContent,
  options: SendOptions,
): Promise<PromptDeliveryFate> {
  const mode = options.mode ?? PromptDeliveryMode.queue;
  state.last_touched_monotonic = host.monotonic_now();
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
    requested_mode: mode,
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
    reconciles_sequence: options.reconciles_sequence ?? null,
  };
  const duplicate = await admit_sender(host, state, message);
  if (duplicate) {
    host.start_admission.reuse_outcome(message.sender_message_id, {
      queued: duplicate instanceof PromptDeliveryQueued,
    });
    return duplicate;
  }
  host.start_admission.handoff(message.sender_message_id);
  try {
    const fate =
      mode === PromptDeliveryMode.steer
        ? await controls.steer(host, state, message)
        : mode === PromptDeliveryMode.send_now
          ? await controls.send_now(host, state, message)
          : await host.queue(state, message);
    if (
      options.record_refusal &&
      fate instanceof PromptDeliveryRefused &&
      message.sender_message_id !== null &&
      host.options.store.sender_message_outcome(
        state.record.conversation_id,
        message.sender_message_id,
      ) === null
    ) {
      await state.lock.run(async () => {
        const rows = await host.persist(state, () =>
          host.options.store.append_events(
            state.record.conversation_id,
            [refused_row(message, mode, fate.refusal_reason)],
            {
              recording_receipt: message.recording_receipt,
              owner_read_through_sequence: message.acknowledge_owner_on_failure ? boundary : null,
            },
          ),
        );
        host.note_written(state, rows);
      });
    }
    if (
      !message.acknowledge_owner_on_failure &&
      boundary !== null &&
      fate instanceof PromptDeliveryQueued
    ) {
      await state.lock.run(async () => {
        const record = await host.persist(state, () =>
          host.options.store.advance_owner_read_through_sequence(
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
      await host.settle_automatic_ids(state, [message.sender_message_id]);
    else if (fate instanceof PromptDeliveryUncertain) {
      if (host.start_admission.attempted([message.sender_message_id])) {
        if (message.sender_message_id !== null)
          host.start_admission.uncertain(message.sender_message_id);
      } else await host.settle_automatic_ids(state, [message.sender_message_id]);
    }
    if (
      fate instanceof PromptDeliveryQueued &&
      state.phase === 'idle' &&
      !state.child_is_quarantined
    )
      host.schedule_drain(state);
    return fate;
  } catch (error) {
    await host.persist(state, () =>
      host.start_admission.failed_sender(
        host.options.store.start_admission_database,
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

async function admit_sender(
  host: SqliteProcessConversationSystem,
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
    const outcome = host.options.store.sender_message_outcome(
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

export async function queue(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  message: Message,
): Promise<PromptDeliveryFate> {
  const admission = await state.lock.run(async () => {
    if (state.restart_retired)
      throw new PlannerError('retry_later', 'This Worker conversation was restarted');
    if (
      state.being_killed ||
      state.child_is_quarantined ||
      state.phase !== 'idle' ||
      state.held_prompts.length > 0 ||
      state.late_steers.size > 0
    )
      return host.hold_prompt(state, message);
    if (!host.start_admission.begin_queued([message.sender_message_id])) {
      const fate = host.hold_prompt(state, message);
      host.defer_start_drain(state);
      return fate;
    }
    return host.reserve_turn(state, false, [message.sender_message_id]);
  });
  if (admission instanceof PromptDeliveryQueued) return admission;
  return host.deliver_and_finalize(state, admission, [message], false);
}

export function hold_prompt(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  message: Message,
  position = state.held_prompts.length,
): PromptDeliveryQueued {
  const held: Message = {
    ...message,
    // A message held again keeps the identity the queue already showed.
    held_prompt_id: message.held_prompt_id || `held_${randomUUID().replaceAll('-', '')}`,
    snapshot_sent_at_unix_milliseconds:
      message.sent_at_unix_milliseconds ?? Math.floor(host.unix_time_now() * 1000),
  };
  note_pending_owner_sends(
    host.options.store.start_admission_database,
    state.record.conversation_id,
    [held],
    PromptDeliveryMode.queue,
    false,
  );
  host.start_admission.queue(message.sender_message_id);
  state.held_prompts.splice(position, 0, held);
  host.mark_eligible_held(state);
  host.publish_held_prompts_changed(state);
  return new PromptDeliveryQueued({ queue_position: position + 1 });
}

export function finish_held_deliveries(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  batch: readonly Message[],
): void {
  for (const message of batch) {
    if (message.sender_message_id === null) continue;
    const admission = state.sender_messages_being_delivered.get(message.sender_message_id);
    state.sender_messages_being_delivered.delete(message.sender_message_id);
    admission?.settled.resolve();
  }
}

function deprioritize_held(host: SqliteProcessConversationSystem, state: ConversationState): void {
  for (const message of state.held_prompts)
    if (!message.delivery_uncertain)
      host.start_admission.wait_if_unadmitted(message.sender_message_id);
}

export function mark_eligible_held(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): void {
  if (
    !['idle', 'draining'].includes(state.phase) ||
    state.child_is_quarantined ||
    state.late_steers.size ||
    state.held_prompts.some((message) => message.delivery_uncertain)
  ) {
    deprioritize_held(host, state);
    return;
  }
  const head = leading_run_that_can_share_a_turn(state.held_prompts);
  for (const message of state.held_prompts.slice(head.length))
    host.start_admission.wait_if_unadmitted(message.sender_message_id);
  host.start_admission.mark_eligible(head.map((message) => message.sender_message_id));
}

export function defer_start_drain(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): void {
  if (host.shutting_down || state.child_is_quarantined) return;
  host.deferred_start_conversations.add(state.record.conversation_id);
  if (host.deferred_start_timer !== null) return;
  host.deferred_start_timer = setTimeout(
    () => {
      host.deferred_start_timer = null;
      if (host.shutting_down) return;
      const pending = [...host.deferred_start_conversations].flatMap((identity) => {
        const current = host.conversations.get(identity);
        return current ? [current] : [];
      });
      host.deferred_start_conversations.clear();
      for (const current of pending) host.mark_eligible_held(current);
      pending.sort((first, second) => {
        const a = host.start_admission.priority(
          first.held_prompts.map((message) => message.sender_message_id),
        );
        const b = host.start_admission.priority(
          second.held_prompts.map((message) => message.sender_message_id),
        );
        return a[0] - b[0] || a[1] - b[1];
      });
      for (const current of pending) {
        const identity = current.record.conversation_id;
        if (
          host.deferred_start_preparing.has(identity) ||
          host.deferred_start_preparing.size >= host.start_admission.limit
        ) {
          host.defer_start_drain(current);
          continue;
        }
        host.deferred_start_preparing.add(identity);
        void own_settlement(current, host.drain_held_prompts(current))
          .finally(() => {
            host.deferred_start_preparing.delete(identity);
          })
          .catch((error) =>
            console.error(`conversation ${identity} could not retry its deferred start`, error),
          );
      }
    },
    host.start_admission.poll_delay(1) * 1000,
  );
}

export function schedule_drain(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): void {
  if (host.shutting_down || state.restart_retired) return;
  host.deferred_start_conversations.delete(state.record.conversation_id);
  host.mark_eligible_held(state);
  void own_settlement(state, host.drain_held_prompts(state)).catch((error) =>
    console.error(
      `conversation ${state.record.conversation_id} could not drain held prompts`,
      error,
    ),
  );
}

export async function drain_held_prompts(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  if (host.shutting_down || state.restart_retired) return;
  await host.ensure_local_custody(state);
  if (
    workspace_is_retiring(host, state) ||
    host.options.store.blocked_local_conversations.has(state.record.conversation_id)
  )
    return;
  host.require_runtime_ownership();
  if (host.execution_refusal(state.record.conversation_id) !== null) {
    deprioritize_held(host, state);
    return;
  }
  const taken = await state.lock.run(() => {
    if (state.restart_retired) return false;
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
    if (state.phase !== 'draining') deprioritize_held(host, state);
    return;
  }
  while (true) {
    const next = await state.lock.run(async () => {
      if (host.shutting_down || host.execution_refusal(state.record.conversation_id) !== null) {
        set_phase(state, 'idle');
        deprioritize_held(host, state);
        return null;
      }
      if (state.being_killed || state.child_is_quarantined || state.held_prompts.length === 0) {
        set_phase(state, 'idle');
        host.mark_eligible_held(state);
        return null;
      }
      const batch = leading_run_that_can_share_a_turn(state.held_prompts);
      host.mark_eligible_held(state);
      if (!host.start_admission.begin_queued(batch.map((message) => message.sender_message_id))) {
        set_phase(state, 'idle');
        host.defer_start_drain(state);
        return null;
      }
      try {
        await host.persist(state, () =>
          host.options.store.mark_held_sender_messages_leaving_queue(
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
        host.publish_held_prompts_changed(state);
        return {
          batch,
          reservation: await host.reserve_turn(
            state,
            false,
            batch.map((message) => message.sender_message_id),
          ),
        };
      } catch (error) {
        host.finish_held_deliveries(state, batch);
        for (const message of [...batch].reverse()) {
          host.start_admission.queue(message.sender_message_id);
          if (!state.held_prompts.includes(message)) state.held_prompts.unshift(message);
        }
        set_phase(state, 'idle');
        host.mark_eligible_held(state);
        host.defer_start_drain(state);
        throw error;
      }
    });
    if (!next) return;
    const fate = await host.deliver_and_finalize(state, next.reservation, next.batch, true);
    if (fate instanceof PromptDeliveryStarted || fate instanceof PromptDeliveryUncertain) return;
  }
}

export async function record_events_transaction<T>(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
  operation: (
    tx: import('../../core/database/contracts.ts').TransactionSql,
  ) => import('../record/contracts.ts').EventMutation<T>,
): Promise<T> {
  return host.workspace_admission.operation(async (admitted) => {
    if (!admitted) throw new Error('Workspace admission is closed for an owner update.');
    const state = host.conversation_state(conversation_id);
    if (!state) throw new Error('conversation is absent');
    return own_settlement(
      state,
      state.lock.run(async () => {
        if (host.shutting_down) throw new DOMException('The operation was aborted', 'AbortError');
        const result = await contention_only_write(() =>
          host.options.store.transact_events(conversation_id, operation),
        );
        host.note_written(state, result.written);
        return result.value;
      }),
    );
  });
}

export async function record_message_to_owner(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
  content: MessageContent,
  options: RecordMessageToOwnerOptions,
): Promise<void> {
  require_message_content(content);
  const state = host.conversation_state(conversation_id);
  if (!state) throw new Error('no such conversation');
  return own_settlement(
    state,
    state.lock.run(async () => {
      if (options.sender_message_id != null) {
        const outcome = host.options.store.sender_message_outcome(
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
      const rows = await host.persist(state, () =>
        host.options.store.append_events(
          conversation_id,
          [new MessageToOwnerEventPayload({ ...options, content })],
          { recording_receipt: options.recording_receipt },
        ),
      );
      host.note_written(state, rows);
    }),
  );
}
