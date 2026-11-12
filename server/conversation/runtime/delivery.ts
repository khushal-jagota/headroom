/** A local prompt crosses the provider boundary once, then settles only its record. */
import {
  BackendPromptRejected,
  BackendSpawnFailed,
  DeliveryCancelled,
  NeedsRebind,
  PromptWriteRefused,
  PromptWriteFailed,
  SessionLoadFailed,
} from '../backends/contracts.ts';
import type { BackendChild } from '../backends/contracts.ts';
import { PromptDeliveryRefusalReason as R, ResolvedConversationStart } from '../contracts.ts';
import { PromptDiscardedEventPayload } from '../events.ts';
import { one_prompt_from } from '../logic/held_line.ts';
import { prefix_message_content_text } from '../message_content.ts';
import type { MessageContent } from '../message_content.ts';
import type { DeliveryAttempt, DeliveryServices, Message, ReservedTurn } from './contracts.ts';
import type { ConversationState } from './state.ts';
import { ROLE_TEXT_PROMPT_SEPARATOR } from './configuration.ts';
import { same_turn } from './state.ts';

function result(values: Partial<DeliveryAttempt> = {}): DeliveryAttempt {
  return {
    refusal_reason: null,
    refusal_detail: null,
    record_refusal: false,
    suppression_reason: null,
    composed_content_delivered: true,
    messages: [],
    automatic_notice: false,
    ...values,
  };
}
function owned(host: DeliveryServices, state: ConversationState, reservation: ReservedTurn): void {
  if (
    host.shutting_down ||
    state.reserved_turn !== reservation ||
    (state.last_ended_turn_token !== null &&
      state.last_ended_turn_token.turn_number >= reservation.token.turn_number)
  ) {
    throw new DeliveryCancelled();
  }
}
export async function stop_child(
  state: ConversationState,
  child: BackendChild,
  native_operation: <T>(operation: () => Promise<T>) => Promise<T> = (operation) => operation(),
): Promise<boolean> {
  try {
    await native_operation(() => child.stop());
  } catch (error) {
    state.child_is_quarantined = true;
    console.error(`conversation ${state.record.conversation_id} could not stop its child`, error);
    return false;
  }
  if (state.child === child) {
    state.child = null;
    state.child_event_sink = null;
    state.child_is_quarantined = false;
  }
  return true;
}
async function ensure_child(
  host: DeliveryServices,
  state: ConversationState,
  reservation: ReservedTurn,
  message: Message,
): Promise<BackendChild> {
  if (state.child_is_quarantined) throw new BackendSpawnFailed(state.record.conversation_id);
  if (state.child !== null) return state.child;
  const previous = state.record.resolved_start();
  const resolved = new ResolvedConversationStart({
    ...previous,
    model: message.model_change ?? previous.model,
    reasoning_effort:
      message.reasoning_effort_change === null && !message.reasoning_effort_is_set
        ? previous.reasoning_effort
        : message.reasoning_effort_change,
  });
  const sink = host.make_sink(state);
  state.child_event_sink = sink;
  const child = host.options.backend_child_factories[resolved.backend_key]({
    resolved_start: resolved,
    event_sink: sink,
    message_files: host.options.message_files,
  });
  // Retained remote child ownership belongs to the executor unit.
  if (child.stop_outside_lock)
    throw new BackendSpawnFailed('Backend did not retain its run identity');
  state.local_child_retirement_proven = false;
  state.preparing_child = child;
  try {
    await host.native_operation(() =>
      child.start(resolved, { vendor_session_cursor: state.record.vendor_session_cursor }),
    );
  } finally {
    if (state.preparing_child === child) state.preparing_child = null;
  }
  if (host.shutting_down || state.reserved_turn !== reservation) {
    if (!host.shutting_down) await host.discard_child(state, child);
    throw new DeliveryCancelled();
  }
  state.child = child;
  return child;
}
async function compose(
  host: DeliveryServices,
  state: ConversationState,
  content: MessageContent,
): Promise<MessageContent> {
  const role = state.record.role_text;
  if (new Map(state.record.identity_environment_variables).get('PLAN_ACTOR') === 'manager') {
    return prefix_message_content_text(content, role ?? '', ROLE_TEXT_PROMPT_SEPARATOR);
  }
  if (role === null || state.has_delivered_prompt) return content;
  if (
    await host.persist(state, () =>
      host.options.store.has_delivered_prompt(state.record.conversation_id),
    )
  ) {
    state.has_delivered_prompt = true;
    return content;
  }
  return prefix_message_content_text(content, role, ROLE_TEXT_PROMPT_SEPARATOR);
}
export async function deliver_prompt(
  host: DeliveryServices,
  state: ConversationState,
  reservation: ReservedTurn,
  batch: readonly Message[],
): Promise<DeliveryAttempt> {
  const first = batch[0];
  const requested = first.model_change ?? state.record.model;
  if (
    requested !== null &&
    host.options.model_is_enabled?.(state.record.backend_key, requested) === false
  ) {
    return result({
      refusal_reason: R.model_resolution_failed,
      refusal_detail: `Model ${requested} is disabled`,
      record_refusal: true,
    });
  }
  const had_child = state.child !== null;
  let child: BackendChild;
  try {
    child = await ensure_child(host, state, reservation, first);
  } catch (error) {
    if (error instanceof BackendSpawnFailed) {
      return result({
        refusal_reason: R.backend_did_not_start,
        refusal_detail: error.message,
        record_refusal: true,
      });
    }
    if (error instanceof SessionLoadFailed)
      return result({ refusal_reason: R.session_did_not_load });
    throw error;
  }
  const decide = () =>
    state.lock.run(async () => {
      const decisions = await host.persist(state, () =>
        host.options.store.admit_sender_messages(
          state.record.conversation_id,
          batch.map((message) => new PromptDiscardedEventPayload({ ...message })),
          {
            lifecycle_if_all_suppressed: [
              {
                kind: 'turn',
                identity: String(reservation.token.turn_number),
                turn_number: reservation.token.turn_number,
                operation: 'turn',
                status: 'unavailable',
                provider_identity: null,
              },
              {
                kind: 'attempt',
                identity: `prompt:${reservation.token.turn_number}`,
                turn_number: reservation.token.turn_number,
                operation: 'prompt',
                status: 'suppressed',
                provider_identity: null,
              },
            ],
          },
        ),
      );
      host.note_written(
        state,
        host.options.store.read_events_after(
          state.record.conversation_id,
          state.record.latest_sequence,
        ),
      );
      return decisions;
    });
  const decisions = await decide();
  let survivors = batch.filter((_message, index) => decisions[index].suppression_reason === null);
  if (survivors.length === 0) {
    await host.discard_child(state, child);
    return result({
      suppression_reason:
        decisions.find((decision) => decision.suppression_reason)?.suppression_reason ?? null,
    });
  }
  let automatic_notice = decisions
    .filter((decision) => decision.suppression_reason === null)
    .every((decision) => decision.is_manager_notice);
  let sender_content = one_prompt_from(survivors);
  let wire_content = await compose(host, state, sender_content);
  const write = async (target: BackendChild) => {
    const accepted = await host.native_operation(() =>
      target.write_prompt(reservation.token, wire_content, {
        sender_label: survivors[0].sender_label,
        sender_content,
        sender_message_count: survivors.length,
        mode: reservation.mode,
        model_change: first.model_change,
        reasoning_effort_change: first.reasoning_effort_change,
        reasoning_effort_is_set: first.reasoning_effort_is_set,
        automatic_compaction: reservation.automatic_compaction,
      }),
    );
    reservation.prompt_accepted = true;
    return result({
      messages: survivors,
      automatic_notice,
      composed_content_delivered: accepted.composed_content_delivered,
    });
  };
  let rebound = false;
  try {
    if (
      had_child &&
      first.reasoning_effort_is_set &&
      first.reasoning_effort_change === null &&
      state.record.reasoning_effort !== null
    ) {
      throw new NeedsRebind('exact desired default or alias target requires a fresh child');
    }
    return await write(child);
  } catch (error) {
    if (!(error instanceof NeedsRebind)) return refusal_or_throw(error);
    rebound = true;
    await state.lock.run(() => {
      owned(host, state, reservation);
      if (state.child !== child) throw new DeliveryCancelled();
      state.child_is_quarantined = true;
    });
    if (!(await host.discard_child(state, child)))
      return result({ refusal_reason: R.write_to_backend_failed });
    owned(host, state, reservation);
    try {
      child = await ensure_child(host, state, reservation, first);
      owned(host, state, reservation);
      // Preparation can outlive an owner decision. Recheck the original envelopes.
      const repeated = await decide();
      survivors = batch.filter((_message, index) => repeated[index].suppression_reason === null);
      if (survivors.length === 0) {
        await host.discard_child(state, child);
        return result({
          suppression_reason:
            repeated.find((decision) => decision.suppression_reason)?.suppression_reason ?? null,
        });
      }
      automatic_notice = repeated
        .filter((decision) => decision.suppression_reason === null)
        .every((decision) => decision.is_manager_notice);
      sender_content = one_prompt_from(survivors);
      wire_content = await compose(host, state, sender_content);
      owned(host, state, reservation);
      return await write(child);
    } catch (next) {
      if (next instanceof PromptWriteFailed) throw next;
      await host.discard_child(state, child);
      if (next instanceof NeedsRebind)
        return result({
          refusal_reason: R.write_to_backend_failed,
          record_refusal: error.failed_child_recovery,
        });
      const attempt = refusal_or_throw(next);
      return { ...attempt, record_refusal: attempt.record_refusal || error.failed_child_recovery };
    } finally {
      if (rebound && !same_turn(state.reserved_turn?.token ?? null, reservation.token)) {
        // The replacement belongs only to this exact admitted delivery.
        await host.discard_child(state, child);
      }
    }
  }
}
function refusal_or_throw(error: unknown): DeliveryAttempt {
  if (error instanceof PromptWriteRefused)
    return result({ refusal_reason: R.write_to_backend_failed, refusal_detail: error.message });
  if (error instanceof BackendPromptRejected)
    return result({
      refusal_reason: R.backend_rejected_prompt,
      refusal_detail: error.message,
      record_refusal: true,
    });
  if (error instanceof BackendSpawnFailed)
    return result({
      refusal_reason: R.backend_did_not_start,
      refusal_detail: error.message,
      record_refusal: true,
    });
  if (error instanceof SessionLoadFailed) return result({ refusal_reason: R.session_did_not_load });
  throw error;
}
