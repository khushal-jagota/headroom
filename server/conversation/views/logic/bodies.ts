/** Resolve a bounded view row from canonical source and settlement records. */
import { readable_tool_call_detail } from '../../tool_detail.ts';
import type { ConversationBackendKey } from '../../contracts.ts';
import type { ConversationEventPayload, PromptEventPayload } from '../../events.ts';
import type { MessageContent } from '../../message_content.ts';
import { MessageFile, MessageImage, MessageText } from '../../message_content.ts';
import {
  SENT_AFTER_ROW_LIMIT_MILLISECONDS,
  SENT_BEFORE_ROW_LIMIT_MILLISECONDS,
  TOOL_DETAIL_CHARACTERS,
} from '../configuration.ts';
import type {
  AskDeadReason,
  PermissionRowBody,
  UserInputRowBody,
  ViewMessagePiece,
  ViewRowBody,
} from '../contracts.ts';
export function content_pieces(content: MessageContent): ViewMessagePiece[] {
  return content.map((piece) => {
    if (piece instanceof MessageText) {
      return { piece: 'text', text: piece.text };
    }
    if (piece instanceof MessageImage) {
      return {
        piece: 'image',
        stored_file_id: piece.stored_file_id,
        media_type: piece.media_type,
        file_name: piece.file_name,
      };
    }
    if (piece instanceof MessageFile) {
      return {
        piece: 'file',
        stored_file_id: piece.stored_file_id,
        media_type: piece.media_type,
        file_name: piece.file_name,
        byte_count: piece.byte_count,
      };
    }
    throw new Error('message content contains an unknown piece');
  });
}
export function sent_at_milliseconds(payload: PromptEventPayload, created_at: number): number {
  const written = created_at * 1000;
  const minted = payload.sent_at_unix_milliseconds;
  if (minted === null) {
    return written;
  }
  const difference = minted - written;
  if (
    difference < -SENT_BEFORE_ROW_LIMIT_MILLISECONDS ||
    difference > SENT_AFTER_ROW_LIMIT_MILLISECONDS
  ) {
    return written;
  }
  return minted;
}
export function in_focus(payload: ConversationEventPayload, owner_label = 'owner'): boolean {
  switch (payload.kind) {
    case 'prompt':
    case 'prompt_delivery_refused':
    case 'prompt_delivery_uncertain':
    case 'prompt_discarded':
      return (
        payload.sender?.kind === 'owner' ||
        (payload.sender === null &&
          payload.recipient === null &&
          payload.sender_label === owner_label)
      );
    case 'message_to_owner':
      return payload.recipient.kind === 'owner';
    case 'turn_ended':
      return payload.ending !== 'completed';
    case 'credential_request':
    case 'permission_asked':
    case 'permission_answered':
    case 'user_input_requested':
    case 'user_input_answered':
    case 'user_input_failed':
      return true;
    default:
      return false;
  }
}
export interface RowBodyOptions {
  created_at: number;
  settlement?: ConversationEventPayload | null;
  settlement_sequence?: number | null;
  dead_reason?: AskDeadReason | null;
  full_tool_detail?: boolean;
  backend_key?: ConversationBackendKey;
}
export function row_body(
  source: ConversationEventPayload,
  options: RowBodyOptions,
): ViewRowBody | null {
  const {
    created_at,
    settlement = null,
    settlement_sequence = null,
    dead_reason = null,
    full_tool_detail = false,
    backend_key = 'hermes',
  } = options;
  switch (source.kind) {
    case 'credential_request':
      return settlement?.kind === 'credential_request' ? settlement.request : source.request;
    case 'prompt':
      return {
        kind: 'prompt',
        content: content_pieces(source.content),
        sender_label: source.sender_label,
        mode: source.mode,
        sent_at_unix_milliseconds: sent_at_milliseconds(source, created_at),
      };
    case 'prompt_delivery_refused':
      return {
        kind: 'prompt_refused',
        content: content_pieces(source.content),
        sender_label: source.sender_label,
        reason: source.refusal_reason,
        refusal_detail: source.refusal_detail,
      };
    case 'prompt_delivery_uncertain':
    case 'prompt_discarded':
      return {
        kind: source.kind === 'prompt_delivery_uncertain' ? 'prompt_uncertain' : 'prompt_discarded',
        content: content_pieces(source.content),
        sender_label: source.sender_label,
      };
    case 'agent_message':
    case 'message_to_owner':
      return {
        kind: 'agent_message',
        content: content_pieces(source.content),
        to_owner: source.kind === 'message_to_owner',
      };
    case 'tool_call_started':
    case 'tool_call_finished': {
      const start = source.kind === 'tool_call_started' ? source : null;
      const finish =
        settlement?.kind === 'tool_call_finished'
          ? settlement
          : source.kind === 'tool_call_finished'
            ? source
            : null;
      const finished_detail =
        finish === null ? null : readable_tool_call_detail(finish.detail, { backend_key });
      const detail = finished_detail ?? start?.detail ?? null;
      const capped =
        finish !== null &&
        detail !== null &&
        Array.from(detail).length > TOOL_DETAIL_CHARACTERS &&
        !full_tool_detail;
      return {
        kind: 'tool_call',
        tool_call_id: source.tool_call_id,
        title: start?.title ?? source.tool_call_id,
        tool_kind: start?.tool_kind ?? 'other',
        detail: capped ? Array.from(detail!).slice(0, TOOL_DETAIL_CHARACTERS).join('') : detail,
        started_detail: start?.detail ?? null,
        capped_detail_sequence: capped ? settlement_sequence : null,
        status: finish?.tool_call_status ?? 'running',
      };
    }
    case 'permission_asked': {
      const answer = settlement?.kind === 'permission_answered' ? settlement : null;
      const body = {
        kind: 'permission_ask',
        ask_id: source.ask_id,
        title: source.title,
        detail: source.detail,
        options: source.options.map((option) => ({ ...option })),
        state: answer !== null ? 'answered' : dead_reason !== null ? 'dead' : 'live',
        dead_reason: answer !== null ? null : dead_reason,
        answered_option_label:
          answer === null
            ? null
            : (source.options.find((option) => option.option_id === answer.option_id)?.label ??
              answer.option_id),
      } as PermissionRowBody;
      if (answer?.answer_id) {
        body.answer_fate =
          answer.fate === 'saved' ? (dead_reason !== null ? 'unconfirmed' : 'saved') : 'applied';
      }
      return body;
    }
    case 'user_input_requested':
    case 'user_input_failed': {
      const answer = settlement?.kind === 'user_input_answered' ? settlement : null;
      const failure =
        settlement?.kind === 'user_input_failed'
          ? settlement
          : source.kind === 'user_input_failed'
            ? source
            : null;
      const body = {
        kind: 'user_input',
        request_id: source.request_id,
        questions:
          source.kind === 'user_input_requested'
            ? source.questions.map((question) => ({
                ...question,
                options: question.options.map((option) => ({ ...option })),
              }))
            : [],
        state:
          answer !== null
            ? 'answered'
            : failure !== null
              ? 'failed'
              : dead_reason !== null
                ? 'dead'
                : 'live',
        dead_reason: answer !== null || failure !== null ? null : dead_reason,
        answers:
          answer === null
            ? null
            : answer.answers.map((value) => ({ ...value, answers: [...value.answers] })),
        failure_detail: failure?.detail ?? null,
      } as UserInputRowBody;
      if (answer?.answer_id) {
        body.answer_fate =
          answer.fate === 'saved' ? (dead_reason !== null ? 'unconfirmed' : 'saved') : 'applied';
      }
      return body;
    }
    case 'model_changed':
      return {
        kind: 'model_changed',
        model: source.model,
        reasoning_effort: source.reasoning_effort,
      };
    case 'token_usage':
      return {
        kind: 'token_usage',
        input_tokens: source.input_tokens,
        output_tokens: source.output_tokens,
        cached_input_tokens: source.cached_input_tokens,
        cost_usd: source.cost_usd,
      };
    case 'context_compacted':
      return { kind: 'context_compacted' };
    case 'command_reply':
      return { kind: 'command_reply', reply_text: source.reply_text };
    case 'turn_ended':
      return {
        kind: 'turn_ended',
        ending: source.ending,
        error_summary: source.error_summary,
        automatic_compaction_result: source.automatic_compaction_result,
      };
    default:
      return null;
  }
}
