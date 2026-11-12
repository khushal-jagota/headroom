/** Known contents of open wire dictionaries. Generic envelopes never validate these eagerly. */
import type { Principal } from '../core/contracts.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
import type { TicketEdit } from '../tickets/contracts.ts';
import type * as Wire from './wire.generated.ts';
import { decode_value } from './protocol_values.ts';

export interface EnvelopePayloads {
  backend_event: Wire.BackendEvent;
  cli_write: Wire.CliWrite;
  child_stopped: Wire.CommandOutcome;
  answer_application: Wire.AnswerApplication;
}
export interface EventArgumentPayloads {
  agent_message_delta: Wire.AgentMessageDelta;
  model_thinking_happened: Wire.ModelThinkingHappened;
  agent_message_completed: Wire.AgentMessageCompleted;
  command_reply_reported: Wire.CommandReplyReported;
  tool_call_started: Wire.ToolCallStarted;
  tool_call_progress: Wire.ToolCallProgress;
  tool_call_finished: Wire.ToolCallFinished;
  plan_updated: Wire.PlanUpdated;
  token_usage_reported: Wire.TokenUsageReported;
  context_compacted: Wire.ContextCompacted;
  permission_ask_raised: Wire.PermissionAskRaised;
  user_input_requested: Wire.UserInputRequested;
  user_input_failed: Wire.UserInputFailed;
  turn_ended: Wire.TurnEnded;
  vendor_session_cursor_rebound: Wire.VendorSessionCursorRebound;
  composer_catalog_reported: Wire.ComposerCatalogReported;
  background_task_started: Wire.BackgroundTaskStarted;
  background_task_ended: Wire.BackgroundTaskEnded;
  backend_stream_ended: Wire.BackendStreamEnded;
}
/** Python accepts additional keys and extensible error/reason text in these dictionaries. */
export type OpenPayload<T> = NumericJSON<T> & NumericJSON<Wire.JsonObject>;
export interface CommandResultPayloads {
  prompt_accepted: { kind: 'prompt_accepted'; composed_content_delivered: boolean };
  steer_accepted: { kind: 'steer_accepted'; composed_content_delivered: boolean };
  steer_uncertain: { kind: 'steer_uncertain'; composed_content_delivered: boolean | null };
  steer_refused: { kind: 'steer_refused'; refusal_reason: string };
  answer_application: { answer_application: Wire.AnswerApplication };
  error: { error_type: string; error_message: string };
  settlement: { reason: string };
}
export type CommandResultPayload =
  OpenPayload<CommandResultPayloads[keyof CommandResultPayloads]> | NumericJSON<Wire.JsonObject>; // Start, stop and cancel can return an empty dictionary.
export interface ReceiptResultPayloads {
  error: { error: { code: string; message: string; detail: Wire.JsonObject } };
  delivery: {
    target: Principal;
    conversation_id: string | null;
    fate: string;
    refusal_reason?: string;
  };
  blockers: { collection: 'blockers'; container_id: string; member_id: string; ok: boolean };
  ticket: Wire.JsonObject; // The canonical Ticket view belongs to the Ticket writer.
}
export interface CliArgumentPayloads {
  'ticket.create': { body: Wire.JsonObject };
  'ticket.edit': { ticket_id: string; body: TicketEdit };
  'ticket.propose': { ticket_id: string; body: { body: string } };
  'ticket.block': { ticket_id: string; blocker_id: string; body: Wire.JsonObject };
  'ticket.unblock': { ticket_id: string; blocker_id: string; body: Wire.JsonObject };
  'message.send': { body: { target: Principal; message: string; mode?: Wire.PromptDeliveryMode } };
  'ticket.request_help': {
    ticket_id: string;
    body: { message: string; recipient?: Principal | null };
  };
}
export type ModelListPayload = NumericJSON<Wire.BackendSnapshot>;
const envelope_roots = {
  backend_event: 'BackendEvent',
  cli_write: 'CliWrite',
  child_stopped: 'CommandOutcome',
  answer_application: 'AnswerApplication',
} as const;
/** Invoke at the item's original consumer boundary, after generic batch admission. */
export function decode_envelope_payload<K extends Wire.EnvelopeKind>(
  kind: K,
  payload: unknown,
): NumericJSON<EnvelopePayloads[K]> {
  return decode_value(payload, envelope_roots[kind]) as NumericJSON<EnvelopePayloads[K]>;
}
