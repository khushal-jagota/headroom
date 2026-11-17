import { ticket_id_of } from '../../executor/routing.ts';
import { is_memory_notice_sender_message_id } from '../../runtime/worker_memory.ts';
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
import { prefix_message_content_text, text_message_content } from '../message_content.ts';
import { normalized_failure_summary } from '../logic/backend_events.ts';
import type { MessageContent } from '../message_content.ts';
import type { DeliveryAttempt, DeliveryServices, Message, ReservedTurn } from './contracts.ts';
import type { ConversationState } from './state.ts';
import { ROLE_TEXT_PROMPT_SEPARATOR } from './configuration.ts';
import { Deferred, same_turn } from './state.ts';

function result(values: Partial<DeliveryAttempt> = {}): DeliveryAttempt {
  return {
    refusal_reason: null,
    refusal_detail: null,
    record_refusal: false,
    suppression_reason: null,
    composed_content_delivered: true,
    messages: [],
    automatic_notice: false,
    memory_notice_only: false,
    ...values,
  };
}
function owned(host: DeliveryServices, state: ConversationState, reservation: ReservedTurn): void {
  if (
    host.shutting_down ||
    reservation.shutdown_owned ||
    state.reserved_turn !== reservation ||
    (state.last_ended_turn_token !== null &&
      state.last_ended_turn_token.turn_number >= reservation.token.turn_number)
  ) {
    throw new DeliveryCancelled();
  }
}
async function ensure_child(
  host: DeliveryServices,
  state: ConversationState,
  reservation: ReservedTurn,
  message: Message,
): Promise<BackendChild> {
  await host.ensure_local_custody(state);
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
  if (child.stop_outside_lock) {
    const retained = host.retained_run(state);
    if (retained === null) throw new BackendSpawnFailed('Backend did not retain its run identity');
    state.retained_child_reference = retained.reference;
    state.delivery_uncertain = false;
    state.child = child;
    await host.persist(state, () =>
      host.start_admission.bind_child(
        host.options.store.start_admission_database,
        reservation.token,
        retained.reference,
      ),
    );
  }
  if (!child.stop_outside_lock) await host.record_local_child_preparation(state, child, sink);
  state.preparing_child = child;
  try {
    await host.native_operation(() =>
      child.start(resolved, { vendor_session_cursor: state.record.vendor_session_cursor }),
    );
  } catch (error) {
    if (!child.stop_outside_lock) {
      state.child = child;
      state.child_is_quarantined = true;
      await host.discard_child(state, child);
    }
    throw error;
  } finally {
    if (state.preparing_child === child) state.preparing_child = null;
  }
  if (host.shutting_down || state.reserved_turn !== reservation) {
    if (!host.shutting_down || !child.stop_outside_lock) await host.discard_child(state, child);
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
  batch = batch.filter((message) => !message.dispatch_memory_note);
  const first = batch[0];
  const requested = first.model_change ?? state.record.model;
  const refusal =
    requested === null
      ? null
      : host.options.delivery_model_refusal(
          state.record.backend_key,
          requested,
          ticket_id_of(state.record.resolved_start()),
        );
  if (refusal !== null) {
    return result({
      refusal_reason: R.model_resolution_failed,
      refusal_detail: refusal,
      record_refusal: true,
    });
  }
  const references = batch.flatMap((message) => message.recovery_references);
  const identities = new Map(
    batch.flatMap((message) =>
      message.sender_message_id === null
        ? []
        : message.recovery_references.map(
            (reference) => [reference.interruption_id, message.sender_message_id!] as const,
          ),
    ),
  );
  const interruption_notice = await host.persist(state, () =>
    host.options.store.bind_interruption_delivery(
      state.record.conversation_id,
      reservation.token.turn_number,
      references.length === 0 ? null : references,
      identities.size === 0 ? null : identities,
    ),
  );
  const decide = () =>
    state.lock.run(async () => {
      const decisions = await host.persist(state, () =>
        host.options.store.admit_sender_messages(
          state.record.conversation_id,
          batch.map((message) => new PromptDiscardedEventPayload({ ...message })),
          {
            automatic_compaction: reservation.automatic_compaction,
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
      await host.settle_automatic_ids(
        state,
        batch.flatMap((message, index) =>
          decisions[index].suppression_reason !== null ? [message.sender_message_id] : [],
        ),
      );
      return decisions;
    });
  if (
    batch.length > 0 &&
    (reservation.automatic_compaction ||
      batch.every((message) => is_memory_notice_sender_message_id(message.sender_message_id)))
  ) {
    const before_preparation = await decide();
    if (before_preparation.every((decision) => decision.suppression_reason !== null)) {
      return result({
        suppression_reason:
          before_preparation.find((decision) => decision.suppression_reason)?.suppression_reason ??
          null,
      });
    }
  }
  const had_child = state.child !== null;
  let child: BackendChild;
  try {
    child = await ensure_child(host, state, reservation, first);
  } catch (error) {
    if (error instanceof BackendSpawnFailed) {
      console.error('Conversation backend did not start', {
        conversation_id: state.record.conversation_id,
        backend_key: state.record.backend_key,
        reason: normalized_failure_summary(error.message),
      });
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
  let memory_notice_only = decisions
    .filter((decision) => decision.suppression_reason === null)
    .every((decision) => decision.is_memory_notice);
  const write = async (target: BackendChild) => {
    const reference = state.retained_child_reference;
    if (target.stop_outside_lock && reference !== null)
      await host.persist(state, () =>
        host.start_admission.bind_child(
          host.options.store.start_admission_database,
          reservation.token,
          reference,
        ),
      );
    if (
      !target.stop_outside_lock &&
      !(await host.check_local_child_started(state, reservation.token))
    ) {
      await host.discard_child(state, target);
      return result({
        refusal_reason: R.backend_did_not_start,
        record_refusal: true,
        refusal_detail: 'The agent did not record its process identity. No prompt was sent.',
      });
    }
    survivors = survivors.filter((message) => !message.dispatch_memory_note);
    const note = host.options.store.worker_step_memory_note(
      state.record.conversation_id,
      survivors.flatMap((message) =>
        message.sender_message_id === null ? [] : [message.sender_message_id],
      ),
    );
    let note_added = false;
    if (note !== null)
      survivors = survivors.flatMap((message): Message[] => {
        if (note_added || message.sender_message_id !== note.after_sender_message_id)
          return [message];
        note_added = true;
        return [
          message,
          {
            ...message,
            held_prompt_id: '',
            content: text_message_content(note.text),
            sender_label: 'Panels',
            sender_message_id: note.sender_message_id,
            sent_at_unix_milliseconds: null,
            sender: null,
            recipient: null,
            owner_read_through_sequence: null,
            recording_receipt: null,
            recovery_references: [],
            model_change: null,
            reasoning_effort_change: null,
            reasoning_effort_is_set: false,
            system_generated: true,
            dispatch_memory_note: true,
          },
        ];
      });
    const sender_content = one_prompt_from(survivors);
    let wire_content = await compose(host, state, sender_content);
    if (interruption_notice !== null)
      wire_content = prefix_message_content_text(wire_content, interruption_notice, '\n\n');
    owned(host, state, reservation);
    host.start_admission.bind_source(reservation.token, target);
    reservation.messages = survivors;
    reservation.prompt_write_attempted = true;
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
    if (accepted.agent_acceptance_proven && state.child === target)
      await host.accept_automatic_start(state, reservation.token);
    return result({
      messages: survivors,
      automatic_notice,
      memory_notice_only,
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
    reservation.prompt_write_attempted = false;
    host.start_admission.bind_source(reservation.token, null);
    rebound = true;
    await state.lock.run(() => {
      owned(host, state, reservation);
      if (state.child !== child) throw new DeliveryCancelled();
      state.child_is_quarantined = true;
    });
    if (!(await host.retire_child_for_rebind(state, child)))
      return result({ refusal_reason: R.write_to_backend_failed });
    const ready = await state.lock.run(() => {
      owned(host, state, reservation);
      return state.child === null && !state.child_is_quarantined;
    });
    if (!ready) return result({ refusal_reason: R.write_to_backend_failed });
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
      memory_notice_only = repeated
        .filter((decision) => decision.suppression_reason === null)
        .every((decision) => decision.is_memory_notice);
      owned(host, state, reservation);
      return await write(child);
    } catch (next) {
      if (next instanceof NeedsRebind) reservation.prompt_write_attempted = false;
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

/** A durable never-ran prompt already has its row. Repeat bytes on its original turn only. */
export async function write_replayed_prompt(
  host: DeliveryServices,
  state: ConversationState,
  token: import('../backends/contracts.ts').TurnToken,
  prompt: import('../record/contracts.ts').UndeliveredTurnPrompt,
  check_owner: () => void,
): Promise<void> {
  const reservation: ReservedTurn = {
    token,
    resolved: new Deferred<void>(),
    automatic_compaction: prompt.automatic_compaction,
    automatic_notice: false,
    memory_notice_only: false,
    prompt_accepted: false,
    prompt_write_attempted: false,
    messages: [],
    mode: prompt.mode,
    shutdown_owned: false,
    shutdown_fate: null,
  };
  const release = await state.lock.acquire();
  try {
    check_owner();
    state.reserved_turn = reservation;
    // Its original prompt is already durable, so callbacks need no receipt barrier.
    reservation.resolved.resolve();
  } finally {
    release();
  }
  let child = state.child;
  try {
    if (child === null) {
      const message: Message = {
        held_prompt_id: '',
        content: prompt.sender_content,
        sender_label: prompt.sender_label,
        sender: null,
        recipient: null,
        sender_message_id: null,
        sent_at_unix_milliseconds: null,
        snapshot_sent_at_unix_milliseconds: 0,
        model_change: prompt.model_change,
        reasoning_effort_change: prompt.reasoning_effort_change,
        reasoning_effort_is_set: prompt.reasoning_effort_is_set,
        owner_read_through_sequence: null,
        acknowledge_owner_on_failure: false,
        recovery_references: [],
        recording_receipt: null,
        queue_reason: 'requested',
        delivery_uncertain: false,
        system_generated: true,
      };
      child = await ensure_child(host, state, reservation, message);
    }
    check_owner();
    const reference = state.retained_child_reference;
    if (child.stop_outside_lock && reference !== null)
      await host.persist(state, () =>
        host.start_admission.bind_child(
          host.options.store.start_admission_database,
          token,
          reference,
        ),
      );
    if (!child.stop_outside_lock && !(await host.check_local_child_started(state, token))) {
      await host.discard_child(state, child);
      throw new BackendSpawnFailed(
        'The agent did not record its process identity. No prompt was sent.',
      );
    }
    host.start_admission.bind_source(token, child);
    const accepted = await host.native_operation(() =>
      child!.write_prompt(token, prompt.content, {
        sender_label: prompt.sender_label,
        sender_content: prompt.sender_content,
        sender_message_count: prompt.sender_message_count,
        mode: prompt.mode,
        model_change: prompt.model_change,
        reasoning_effort_change: prompt.reasoning_effort_change,
        reasoning_effort_is_set: prompt.reasoning_effort_is_set,
        automatic_compaction: prompt.automatic_compaction,
      }),
    );
    if (accepted.agent_acceptance_proven && state.child === child)
      await host.accept_automatic_start(state, token);
    check_owner();
  } catch (error) {
    if (error instanceof NeedsRebind) {
      host.start_admission.bind_source(token, null);
      if (child !== null) await host.discard_child(state, child);
    }
    check_owner();
    throw error;
  } finally {
    if (state.reserved_turn === reservation) state.reserved_turn = null;
    reservation.resolved.resolve();
  }
}
