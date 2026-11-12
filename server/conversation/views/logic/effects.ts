/** Classify a source event without storage or framework dependencies. */
import type { ConversationEventPayload } from '../../events.ts';
import { in_focus } from './bodies.ts';
import type { EventEffect } from '../projection_contracts.ts';
export function event_effect(payload: ConversationEventPayload): EventEffect {
  const visible = in_focus(payload);
  const effect = (
    row_kind: EventEffect['row_kind'],
    focus_visible = visible,
    values: Partial<EventEffect> = {},
  ): EventEffect => ({
    row_kind,
    focus_visible,
    lookup_kind: null,
    lookup_id: null,
    settlement: false,
    opens_turn: false,
    closes_turn: false,
    changes_plan: false,
    sender_message_id: null,
    reconciles_sequence: null,
    ...values,
  });
  switch (payload.kind) {
    case 'credential_request':
      return effect('credential_request', true, {
        lookup_kind: 'credential_request',
        lookup_id: payload.request.request_id,
        settlement: true,
      });
    case 'prompt':
      return effect('prompt', visible, {
        opens_turn: payload.mode !== 'steer',
        sender_message_id: payload.sender_message_id,
        reconciles_sequence: payload.reconciles_sequence,
      });
    case 'prompt_delivery_refused':
      return effect('prompt_refused', visible, { sender_message_id: payload.sender_message_id });
    case 'prompt_delivery_uncertain':
      return effect('prompt_uncertain', visible, { sender_message_id: payload.sender_message_id });
    case 'prompt_discarded':
      return effect('prompt_discarded', visible, { sender_message_id: payload.sender_message_id });
    case 'agent_message':
    case 'message_to_owner':
      return effect('agent_message');
    case 'explicit_reply_missing':
      return effect(null, false);
    case 'tool_call_started':
    case 'tool_call_finished':
      return effect('tool_call', false, {
        lookup_kind: 'tool',
        lookup_id: payload.tool_call_id,
        settlement: payload.kind === 'tool_call_finished',
      });
    case 'answer_applied':
      return effect(null, true);
    case 'permission_asked':
      return effect('permission_ask', true, {
        lookup_kind: 'permission',
        lookup_id: payload.ask_id,
      });
    case 'permission_answered':
      return effect(null, true, {
        lookup_kind: 'permission',
        lookup_id: payload.ask_id,
        settlement: true,
      });
    case 'user_input_requested':
      return effect('user_input', true, {
        lookup_kind: 'user_input',
        lookup_id: payload.request_id,
      });
    case 'user_input_answered':
      return effect(null, true, {
        lookup_kind: 'user_input',
        lookup_id: payload.request_id,
        settlement: true,
      });
    case 'user_input_failed':
      return effect('user_input', true, {
        lookup_kind: 'user_input',
        lookup_id: payload.request_id,
        settlement: true,
      });
    case 'plan_updated':
      return effect(null, false, { changes_plan: true });
    case 'model_changed':
      return effect('model_changed', false);
    case 'token_usage':
      return effect('token_usage', false);
    case 'context_compacted':
      return effect('context_compacted', false);
    case 'command_reply':
      return effect('command_reply', true);
    case 'turn_ended':
      return effect('turn_ended', visible, { closes_turn: true });
  }
}
