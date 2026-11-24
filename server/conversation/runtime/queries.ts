/** Questions about live conversation state, and workspace activity. */
import type { Principal } from '../../core/contracts.ts';
import type { OwnerSendProgress } from '../contracts.ts';
import { matches_owner_sender_message } from './messages.ts';
import type { RunningTurn } from '../record/contracts.ts';
import { backend_supports_active_steering, HeldPrompt } from '../contracts.ts';
import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';

export function runtime_turn(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
): RunningTurn | null {
  return host.conversations.get(conversation_id)?.running_turn ?? null;
}

export async function pending_permission_ask_ids(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
): Promise<ReadonlySet<string>> {
  const state = host.conversation_state(conversation_id);
  host.options.require_signal_current?.();
  return new Set(state?.running_turn?.pending_permission_ask_ids ?? []);
}

export async function pending_user_input_request_ids(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
): Promise<ReadonlySet<string>> {
  const state = host.conversation_state(conversation_id);
  host.options.require_signal_current?.();
  return new Set(state?.running_turn?.pending_user_input_questions.keys() ?? []);
}

export async function run_if_idle(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
  operation: () => void,
): Promise<boolean> {
  const state = host.conversation_state(conversation_id);
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

export async function held_prompts(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
): Promise<readonly HeldPrompt[]> {
  const state = host.conversation_state(conversation_id);
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

/** Observe a queue item and its possible handoff under the same conversation lock. */
export async function owner_send_progress(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
  sender_message_id: string,
  recipient: Principal,
): Promise<OwnerSendProgress | null> {
  const state = host.conversation_state(conversation_id);
  if (!state) return null;
  return state.lock.run(() => {
    const position = state.held_prompts.findIndex((message) =>
      matches_owner_sender_message(message, sender_message_id, recipient),
    );
    if (position >= 0) {
      return state.held_prompts[position].delivery_uncertain
        ? { state: 'uncertain' }
        : { state: 'queued', position: position + 1 };
    }
    const admission =
      state.sender_messages_being_delivered.get(sender_message_id) ??
      state.admitted_sender_messages.get(sender_message_id);
    return admission &&
      matches_owner_sender_message(admission.message, sender_message_id, recipient)
      ? { state: 'pending' }
      : null;
  });
}

export async function sender_message_ids_awaiting_outcome(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
): Promise<ReadonlySet<string>> {
  const state = host.conversation_state(conversation_id);
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

export async function is_running(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
): Promise<boolean> {
  host.require_runtime_ownership();
  const state = host.conversations.get(conversation_id);
  host.options.require_signal_current?.();
  return state?.running_turn != null;
}

export function supports_active_steering(
  host: SqliteProcessConversationSystem,
  backend_key: BackendInstanceId,
): boolean {
  return backend_supports_active_steering(host.options.backend_registry, backend_key);
}

export async function has_pending_permission_ask(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
): Promise<boolean> {
  host.require_runtime_ownership();
  const state = host.conversations.get(conversation_id);
  host.options.require_signal_current?.();
  return (state?.running_turn?.pending_permission_ask_ids.size ?? 0) > 0;
}

export async function has_pending_user_input(
  host: SqliteProcessConversationSystem,
  conversation_id: string,
): Promise<boolean> {
  host.require_runtime_ownership();
  const state = host.conversations.get(conversation_id);
  host.options.require_signal_current?.();
  return (state?.running_turn?.pending_user_input_questions.size ?? 0) > 0;
}

export function workspace_activity(
  host: SqliteProcessConversationSystem,
): 'idle' | 'busy' | 'unknown' {
  if (
    host.shutting_down ||
    host.workspace_admission.closed ||
    host.options.store.blocked_local_conversations.size
  )
    return 'unknown';
  if (host.workspace_admission.active) return 'busy';
  for (const state of host.conversations.values()) {
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
  return host.options.store.read_live_background_task_ids().size > 0 ? 'busy' : 'idle';
}
