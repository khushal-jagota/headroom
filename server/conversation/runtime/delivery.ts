import { RecipientDeliveryStale } from '../../message_delivery/contracts.ts';
import {
  recipient_attempts_for,
  mark_recipient_attempt,
  with_recipient_attempts,
} from '../../message_delivery/data/recipient_deliveries.ts';
import { note_pending_owner_sends } from '../record/owner_send_receipts.ts';
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
  BackendAdapterError,
} from '../backends/contracts.ts';
import type { BackendChild, TurnToken } from '../backends/contracts.ts';
import {
  PromptDeliveryRefusalReason as R,
  ResolvedConversationStart,
  PromptDeliveryMode,
  PromptDeliveryUncertain,
  PromptDeliverySuppressed,
  PromptDeliveryRefused,
  PromptDeliveryStarted,
} from '../contracts.ts';
import { PromptDiscardedEventPayload, ModelChangedEventPayload } from '../events.ts';
import { one_prompt_from } from '../logic/held_line.ts';
import { selection_target } from '../logic/selection_target.ts';
import type { BackendSelection } from '../../drivers/core/selection.ts';
import { prefix_message_content_text, text_message_content } from '../message_content.ts';
import { error_text, log_backend_failure } from '../backends/failure_summary.ts';
import { recorded_failure_detail } from '../logic/backend_events.ts';
import type { MessageContent } from '../message_content.ts';
import type { DeliveryAttempt, DeliveryServices, Message, ReservedTurn } from './contracts.ts';
import type { ConversationState } from './state.ts';
import { ROLE_TEXT_PROMPT_SEPARATOR } from './configuration.ts';
import { Deferred } from '../../core/deferred.ts';
import { same_turn, set_phase } from './state.ts';
import type { LifecycleFact } from '../record/lifecycle.ts';
import {
  LifecycleKind as K,
  LifecycleOperation as O,
  LifecycleStatus as S,
} from '../record/lifecycle.ts';
import type { RunningTurn, StoredConversationEvent } from '../record/contracts.ts';
import type { PromptDeliveryFate } from '../contracts.ts';
import { is_shutdown_business_cancellation } from '../shutdown.ts';
import { uncertain_row, refused_row, prompt_row } from './messages.ts';
import { field_mutation } from './save_attempt.ts';
import { ConversationRecord } from '../record/contracts.ts';
import { wait_for_item } from '../../executor/item_budget.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';

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
/**
 * A process that the server started before the switch to machine code can still hold the
 * conversation's vendor session. A Ticket's cleanup recheck guards a Worker. Any other
 * conversation starts no child until that process record is proven retired.
 */
function waits_for_recorded_source(state: ConversationState): boolean {
  return !state.local_child_retirement_proven && !state.local_preparation_cancelled;
}
/** Before a new turn opens, retire an idle recorded process, as a kill does. */
async function retire_recorded_source(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  if (state.child !== null) return;
  await host.ensure_local_custody(state);
  if (state.child_is_quarantined || !waits_for_recorded_source(state)) return;
  if (await host.options.store.retire_idle_recorded_source(state.record.conversation_id))
    state.local_child_retirement_proven = true;
}
async function ensure_child(
  host: DeliveryServices,
  state: ConversationState,
  reservation: ReservedTurn,
  message: Message,
): Promise<BackendChild> {
  await host.ensure_local_custody(state);
  if (state.child_is_quarantined)
    throw new BackendSpawnFailed(
      'The agent stopped after an earlier failure and was not replaced yet.',
    );
  if (state.child !== null) return state.child;
  if (waits_for_recorded_source(state))
    throw new BackendSpawnFailed(
      'An agent process that the server started earlier is not proven ended. Select Stop to end it, then send your message again.',
    );
  const previous = state.record.resolved_start();
  const target = selection_target(message, previous);
  const resolved = new ResolvedConversationStart({
    ...previous,
    // `resolved_start` refuses a record without a model, so the target names one.
    model: target.model!,
    reasoning_effort: target.reasoning_effort,
  });
  const instance = host.options.backend_registry.require_live(resolved.backend_key);
  const sink = host.make_sink(state);
  state.child_event_sink = sink;
  const child = instance.child_factory({
    resolved_start: resolved,
    event_sink: sink,
    message_files: host.options.message_files,
  });
  if (child.confirm_cli_compatible)
    await host.native_operation(() => child.confirm_cli_compatible!());
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
  state.child_selection = {
    child,
    selection: { model: resolved.model, reasoning_effort: resolved.reasoning_effort },
  };
  state.last_session_load_refusal_detail = null;
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
          state.record.conversation_id,
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
  if (reservation.automatic_compaction && !had_child)
    return result({ suppression_reason: 'automatic_compaction_child_stopped' });
  let child: BackendChild;
  try {
    child = await ensure_child(host, state, reservation, first);
  } catch (error) {
    if (error instanceof BackendSpawnFailed || error instanceof SessionLoadFailed)
      return refusal_or_throw(state, error);
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
  // The child's own selection, or the record's for a child started elsewhere (an adopted one).
  // The target is the message's change against it, as the child itself reads the change.
  const selection_of = (target: BackendChild): BackendSelection =>
    state.child_selection?.child === target
      ? state.child_selection.selection
      : { model: state.record.model, reasoning_effort: state.record.reasoning_effort };
  const write = async (target: BackendChild) => {
    const selection = selection_target(first, selection_of(target));
    const reference = state.retained_child_reference;
    if (target.stop_outside_lock && reference !== null)
      await host.persist(state, () =>
        host.start_admission.bind_child(
          host.options.store.start_admission_database,
          reservation.token,
          reference,
        ),
      );
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
    await host.persist(state, () =>
      note_pending_owner_sends(
        host.options.store.start_admission_database,
        state.record.conversation_id,
        survivors,
        reservation.mode,
        true,
      ),
    );
    owned(host, state, reservation);
    host.start_admission.bind_source(reservation.token, target);
    reservation.messages = survivors;
    reservation.prompt_write_attempted = true;
    const recipient_attempts = mark_recipient_attempt(
      host.options.store.start_admission_database,
      state.record.conversation_id,
      survivors,
      reservation.token.turn_number,
      target.reference ? 'mailbox' : 'local',
    );
    const accepted = await with_recipient_attempts(recipient_attempts, () =>
      host.native_operation(() =>
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
      ),
    );
    state.child_selection = { child: target, selection };
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
    // A local child implies a live instance. A remote child's backend may have none on this
    // home; then the machine's adapter refuses a selection its child cannot carry.
    const entry = host.options.backend_registry.get(state.record.backend_key);
    if (had_child && entry?.kind === 'live') {
      const instance = entry.instance;
      const current = selection_of(child);
      const plan = instance.plan_selection_transition({
        current,
        target: selection_target(first, current),
        session_capabilities: instance.capabilities,
      });
      if (plan.type === 'restart_session')
        throw new NeedsRebind(
          `${instance.driver_kind} needs a fresh child for this model or reasoning effort`,
        );
      if (plan.type === 'reject')
        return result({
          refusal_reason: R.backend_rejected_prompt,
          refusal_detail: plan.reason,
          record_refusal: true,
        });
    }
    return await write(child);
  } catch (error) {
    if (!(error instanceof NeedsRebind)) return refusal_or_throw(state, error);
    if (reservation.automatic_compaction)
      return result({ suppression_reason: 'automatic_compaction_child_requires_rebind' });
    // A failed child is a backend failure even when a fresh child takes the prompt.
    if (error.failed_child_recovery)
      log_backend_failure(state.record.conversation_id, state.record.backend_key, error);
    reservation.prompt_write_attempted = false;
    host.start_admission.bind_source(reservation.token, null);
    rebound = true;
    await state.lock.run(() => {
      owned(host, state, reservation);
      if (state.child !== child) throw new DeliveryCancelled();
      state.child_is_quarantined = true;
    });
    if (!(await host.retire_child_for_rebind(state, child)))
      return result({
        refusal_reason: R.write_to_backend_failed,
        refusal_detail: recorded_failure_detail(error.message),
      });
    const ready = await state.lock.run(() => {
      owned(host, state, reservation);
      return state.child === null && !state.child_is_quarantined;
    });
    if (!ready)
      return result({
        refusal_reason: R.write_to_backend_failed,
        refusal_detail: recorded_failure_detail(error.message),
      });
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
        // A failed first child was logged above, so the log line names only the fresh one.
        return backend_refusal(
          state,
          R.write_to_backend_failed,
          next,
          error.failed_child_recovery,
          `${error.message}; the fresh agent also needed a restart: ${next.message}`,
          error.failed_child_recovery ? next.message : undefined,
        );
      const attempt = refusal_or_throw(state, next);
      return { ...attempt, record_refusal: attempt.record_refusal || error.failed_child_recovery };
    } finally {
      if (rebound && !same_turn(state.reserved_turn?.token ?? null, reservation.token)) {
        // The replacement belongs only to this exact admitted delivery.
        await host.discard_child(state, child);
      }
    }
  }
}
/** A backend refusal keeps the backend's own text, redacted, as its detail, and is logged once here. */
function backend_refusal(
  state: ConversationState,
  refusal_reason: NonNullable<DeliveryAttempt['refusal_reason']>,
  error: Error,
  record_refusal: boolean,
  text: string = error.message,
  logged_text: string = text,
): DeliveryAttempt {
  log_backend_failure(state.record.conversation_id, state.record.backend_key, error, logged_text);
  return result({ refusal_reason, refusal_detail: recorded_failure_detail(text), record_refusal });
}
function refusal_or_throw(state: ConversationState, error: unknown): DeliveryAttempt {
  if (error instanceof RecipientDeliveryStale)
    return result({
      refusal_reason: R.no_such_conversation,
      refusal_detail: error.message,
      record_refusal: true,
    });
  // A direct send's caller decides whether to record this refusal. The log keeps its text.
  if (error instanceof PromptWriteRefused)
    return backend_refusal(state, R.write_to_backend_failed, error, false);
  if (error instanceof BackendPromptRejected)
    return backend_refusal(state, R.backend_rejected_prompt, error, true);
  if (error instanceof BackendSpawnFailed)
    return backend_refusal(state, R.backend_did_not_start, error, true);
  if (error instanceof SessionLoadFailed) {
    // A worker step retries a lost session. The same failure is recorded and logged once.
    const detail = recorded_failure_detail(error.message);
    if (detail !== null && detail === state.last_session_load_refusal_detail)
      return result({ refusal_reason: R.session_did_not_load, refusal_detail: detail });
    state.last_session_load_refusal_detail = detail;
    return backend_refusal(state, R.session_did_not_load, error, true);
  }
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

export function new_running_turn(
  reservation: Pick<
    ReservedTurn,
    'token' | 'automatic_compaction' | 'automatic_notice' | 'memory_notice_only'
  >,
): RunningTurn {
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

export async function reserve_turn(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  automatic_compaction = false,
  sender_message_ids: readonly (string | null)[] = [],
): Promise<ReservedTurn> {
  await retire_recorded_source(host, state);
  const requests = host.start_admission.requests_for(sender_message_ids);
  let number: number;
  try {
    number = await host.persist(state, () =>
      host.options.store.reserve_turn(state.record.conversation_id, requests),
    );
  } catch (error) {
    // A cancel just before this left the phase ending. A save that gave up must not keep it there.
    if (state.phase === 'ending')
      set_phase(state, state.running_turn === null ? 'idle' : 'running');
    throw error;
  }
  host.start_admission.bind(sender_message_ids, {
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
  host.mark_eligible_held(state);
  return reserved;
}

/** True when a save of this conversation gave up, or the database is unusable. */
export function save_gave_up(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): boolean {
  return state.save_failure !== null || host.options.store.database_unusable_error !== null;
}

export async function deliver_and_finalize(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  reservation: ReservedTurn,
  batch: readonly Message[],
  queued: boolean,
  drain_after_delivery_exception = true,
): Promise<PromptDeliveryFate> {
  try {
    return await deliver_and_record(
      host,
      state,
      reservation,
      batch,
      queued,
      drain_after_delivery_exception,
    );
  } catch (error) {
    if (
      (error instanceof Error && error.name === 'AbortError') ||
      host.shutting_down ||
      !host.save_gave_up(state)
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
        host.abandon_reservation(state, reservation, 'idle');
      host.finish_held_deliveries(state, batch);
    });
    // The agent can have this prompt, so the sender must not hear that it was not sent.
    if (reservation.prompt_write_attempted) return new PromptDeliveryUncertain();
    throw error;
  }
}

async function deliver_and_record(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  reservation: ReservedTurn,
  batch: readonly Message[],
  queued: boolean,
  drain_after_delivery_exception: boolean,
): Promise<PromptDeliveryFate> {
  reservation.messages = batch;
  host.start_admission.bind(
    batch.map((message) => message.sender_message_id),
    reservation.token,
  );
  const promoted = queued && reservation.mode === PromptDeliveryMode.send_now;
  let delivery: DeliveryAttempt;
  try {
    await host.persist(state, () =>
      host.start_admission.persist_bindings(
        host.options.store.start_admission_database,
        reservation.token,
      ),
    );
    reservation.automatic_notice = await host.persist(state, () =>
      host.options.store.all_sender_messages_are_manager_notices(
        state.record.conversation_id,
        batch.map((message) => message.sender_message_id),
      ),
    );
    delivery = await with_recipient_attempts(
      recipient_attempts_for(
        host.options.store.start_admission_database,
        state.record.conversation_id,
        batch,
      ),
      () => deliver_prompt(host, state, reservation, batch),
    );
  } catch (error) {
    if (reservation.shutdown_owned)
      return reservation.shutdown_fate ?? new PromptDeliveryUncertain();
    if (host.shutting_down) throw error;
    await host.persist(state, () =>
      host.start_admission.failed_attempt(
        host.options.store.start_admission_database,
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
      // The uncertain row keeps a capped copy of the backend's text, and the log keeps it whole.
      if (error instanceof BackendAdapterError)
        log_backend_failure(state.record.conversation_id, state.record.backend_key, error);
      if (queued && !reservation.prompt_write_attempted)
        await host.settle_automatic_ids(
          state,
          batch.map((message) => message.sender_message_id),
        );
      await record_delivery_uncertainty(
        host,
        state,
        reservation,
        attempted,
        batch,
        error instanceof BackendAdapterError ? recorded_failure_detail(error.message) : null,
      );
      return new PromptDeliveryUncertain();
    }
    const recovery_messages = batch.filter((message) => message.recovery_references.length > 0);
    if (recovery_messages.length === 0) {
      await host.persist(state, () =>
        host.options.store.fail_recovery_claims(
          state.record.conversation_id,
          reservation.token.turn_number,
        ),
      );
    } else {
      await state.lock.run(async () => {
        for (const [index, message] of recovery_messages.entries()) {
          const rows = await host.persist(state, () =>
            host.options.store.append_events(
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
          host.note_written(state, rows);
        }
      });
    }
    await state.lock.run(() => {
      if (queued) {
        state.child_is_quarantined = true;
        for (const message of [...attempted].reverse())
          if (!message.dispatch_memory_note)
            state.held_prompts.unshift({ ...message, delivery_uncertain: true });
        host.finish_held_deliveries(state, batch);
      }
      host.abandon_reservation(state, reservation, 'idle');
    });
    if (!queued || promoted) {
      if (drain_after_delivery_exception) host.schedule_drain(state);
      throw error;
    }
    return new PromptDeliveryUncertain();
  }
  if (delivery.suppression_reason !== null) {
    await host.settle_automatic_ids(
      state,
      batch.map((message) => message.sender_message_id),
    );
    await state.lock.run(() => {
      host.abandon_reservation(state, reservation, queued && !promoted ? 'draining' : 'idle');
      host.finish_held_deliveries(state, batch);
    });
    if (!queued || promoted) host.schedule_drain(state);
    return new PromptDeliverySuppressed({ reason: delivery.suppression_reason });
  }
  reservation.automatic_notice = delivery.automatic_notice;
  reservation.memory_notice_only = delivery.memory_notice_only;
  const survivors = delivery.messages.length > 0 ? delivery.messages : batch;
  // A drain credits only the successful admission envelopes returned by delivery.
  // A definite refusal returns none. The remaining refused rows never carry read credit.
  const drain_bounds = delivery.messages
    .filter(
      (message) => message.sender?.kind === 'owner' && message.owner_read_through_sequence !== null,
    )
    .map((message) => message.owner_read_through_sequence!);
  const drain_read_through = drain_bounds.length > 0 ? Math.max(...drain_bounds) : null;
  let receipt_save_gave_up = false;
  let receipt_save_detail: string | null = null;
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
            const rows = await host.persist(state, () =>
              host.options.store.append_events(
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
            host.note_written(state, rows);
          }
        } else {
          await host.persist(state, () =>
            host.options.store.transition_lifecycle(state.record.conversation_id, lifecycle),
          );
        }
        await host.settle_automatic_ids(
          state,
          survivors.map((message) => message.sender_message_id),
        );
        set_phase(state, queued && !promoted ? 'draining' : 'idle');
        return new PromptDeliveryRefused({ refusal_reason: delivery.refusal_reason });
      }
      const first = survivors[0];
      const effort_is_set = survivors.some((message) => message.reasoning_effort_is_set);
      const target = selection_target(
        { ...first, reasoning_effort_is_set: effort_is_set },
        state.record,
      );
      const change =
        first.model_change !== null || first.reasoning_effort_change !== null || effort_is_set
          ? new ModelChangedEventPayload({
              model: target.model!,
              reasoning_effort: target.reasoning_effort,
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
        written = await host.persist(
          state,
          () =>
            host.options.store.append_delivered_prompt(state.record.conversation_id, {
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
        receipt_save_detail = recorded_failure_detail(`Receipt save failed: ${error_text(error)}`);
        console.error(
          `conversation ${state.record.conversation_id} delivered a prompt but could not save its receipt`,
          error,
        );
        return new PromptDeliveryUncertain();
      }
      host.note_written(state, written);
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
      host.finish_held_deliveries(state, reservation.messages);
      if (!host.shutting_down) {
        host.options.emit_change?.();
        if (change && queued) {
          await host.options.on_held_model_change?.(
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
    await record_delivery_uncertainty(
      host,
      state,
      reservation,
      survivors,
      batch,
      receipt_save_detail,
    ).catch((failure: unknown) =>
      console.error(
        `conversation ${state.record.conversation_id} could not record an uncertain delivery`,
        failure,
      ),
    );
    await state.lock.run(() => host.abandon_reservation(state, reservation, 'idle'));
  }
  await state.lock.run(() => host.finish_held_deliveries(state, batch));
  if (fate instanceof PromptDeliveryRefused && (!queued || promoted)) host.schedule_drain(state);
  return fate;
}

export function abandon_reservation(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  reservation: ReservedTurn,
  phase: 'idle' | 'draining',
): void {
  if (state.reserved_turn === reservation) state.reserved_turn = null;
  set_phase(state, phase);
  reservation.resolved.resolve();
}

async function record_delivery_uncertainty(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  reservation: ReservedTurn,
  batch: readonly Message[],
  settle_messages: readonly Message[] = batch,
  failure_detail: string | null = null,
): Promise<void> {
  await state.lock.run(async () => {
    for (const message of batch)
      if (message.sender_message_id !== null)
        host.start_admission.uncertain(message.sender_message_id);
    state.child_is_quarantined = true;
    state.delivery_uncertain = true;
    for (const message of batch) {
      const rows = await host.persist(state, () =>
        host.options.store.append_events(
          state.record.conversation_id,
          [uncertain_row(message, reservation.mode, failure_detail)],
          {
            lifecycle: [prompt_fact(reservation.token, S.unknown)],
            recording_receipt: message.recording_receipt,
          },
        ),
      );
      host.note_written(state, rows);
    }
    host.finish_held_deliveries(state, settle_messages);
    const present = new Set(state.held_prompts.map((message) => message.held_prompt_id));
    for (const message of [...batch].reverse()) {
      if (message.dispatch_memory_note) continue;
      if (message.held_prompt_id && !present.has(message.held_prompt_id))
        state.held_prompts.unshift({ ...message, delivery_uncertain: true });
    }
    set_phase(state, 'idle');
    host.mark_eligible_held(state);
    host.publish_held_prompts_changed(state);
  });
}

export async function wait_for_reservation(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  token: TurnToken | null,
  retry_later_at?: number,
): Promise<void> {
  while (
    state.reserved_turn &&
    !state.reserved_turn.resolved.settled &&
    same_turn(state.reserved_turn.token, token)
  ) {
    if (host.shutting_down) return;
    await wait_for_item(state.reserved_turn.resolved.promise, retry_later_at);
  }
}
