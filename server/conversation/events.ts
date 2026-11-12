/** Stored event payloads and transient frames. The backend seam shares these value types. */
import type { Principal } from '../core/contracts.ts';
import type { CredentialRequestRowBody } from '../../web/src/lib/conversation/view/contracts.ts';
import type { MessageContent } from './message_content.ts';
import {
  AnswerFate,
  PromptDeliveryMode,
  PromptDeliveryRefusalReason,
  DeploymentRecoveryReference,
} from './contracts.ts';

export const ConversationEventKind = {
  credential_request: 'credential_request',
  prompt: 'prompt',
  prompt_delivery_refused: 'prompt_delivery_refused',
  prompt_delivery_uncertain: 'prompt_delivery_uncertain',
  prompt_discarded: 'prompt_discarded',
  agent_message: 'agent_message',
  message_to_owner: 'message_to_owner',
  retired_explicit_reply_missing: 'explicit_reply_missing',
  tool_call_started: 'tool_call_started',
  tool_call_finished: 'tool_call_finished',
  permission_asked: 'permission_asked',
  permission_answered: 'permission_answered',
  answer_applied: 'answer_applied',
  user_input_requested: 'user_input_requested',
  user_input_answered: 'user_input_answered',
  user_input_failed: 'user_input_failed',
  plan_updated: 'plan_updated',
  model_changed: 'model_changed',
  token_usage: 'token_usage',
  context_compacted: 'context_compacted',
  command_reply: 'command_reply',
  turn_ended: 'turn_ended',
} as const;
export type ConversationEventKind =
  (typeof ConversationEventKind)[keyof typeof ConversationEventKind];

export const ConversationTurnEnding = {
  completed: 'completed',
  failed: 'failed',
  interrupted: 'interrupted',
} as const;
export type ConversationTurnEnding =
  (typeof ConversationTurnEnding)[keyof typeof ConversationTurnEnding];

export const AutomaticCompactionResult = { not_compacted: 'not_compacted' } as const;
export type AutomaticCompactionResult =
  (typeof AutomaticCompactionResult)[keyof typeof AutomaticCompactionResult];

export const ToolCallStatus = { completed: 'completed', failed: 'failed' } as const;
export type ToolCallStatus = (typeof ToolCallStatus)[keyof typeof ToolCallStatus];

export const PlanEntryStatus = {
  pending: 'pending',
  in_progress: 'in_progress',
  completed: 'completed',
} as const;
export type PlanEntryStatus = (typeof PlanEntryStatus)[keyof typeof PlanEntryStatus];

export class PlanEntry {
  readonly text: string;
  readonly status: PlanEntryStatus;
  constructor(values: { text: string; status: PlanEntryStatus }) {
    this.text = values.text;
    this.status = values.status;
  }
}

export class PermissionAskOption {
  readonly option_id: string;
  readonly label: string;
  readonly option_kind: string;
  constructor(values: { option_id: string; label: string; option_kind: string }) {
    this.option_id = values.option_id;
    this.label = values.label;
    this.option_kind = values.option_kind;
  }
}

export class UserInputOption {
  readonly label: string;
  readonly description: string;
  constructor(values: { label: string; description: string }) {
    this.label = values.label;
    this.description = values.description;
  }
}

export class UserInputQuestion {
  readonly question_id: string;
  readonly header: string;
  readonly question: string;
  readonly options: readonly UserInputOption[];
  readonly multi_select: boolean;
  readonly allow_other: boolean;
  constructor(values: {
    question_id: string;
    header: string;
    question: string;
    options: readonly UserInputOption[];
    multi_select: boolean;
    allow_other: boolean;
  }) {
    this.question_id = values.question_id;
    this.header = values.header;
    this.question = values.question;
    this.options = values.options;
    this.multi_select = values.multi_select;
    this.allow_other = values.allow_other;
  }
}

export class UserInputAnswer {
  readonly question_id: string;
  readonly answers: readonly string[];
  constructor(values: { question_id: string; answers: readonly string[] }) {
    this.question_id = values.question_id;
    this.answers = values.answers;
  }
}

export class PromptEventPayload {
  readonly kind = ConversationEventKind.prompt;
  readonly content: MessageContent;
  readonly sender_label: string;
  readonly mode: PromptDeliveryMode;
  readonly sender_message_id: string | null;
  readonly sent_at_unix_milliseconds: number | null;
  readonly sender: Principal | null;
  readonly recipient: Principal | null;
  readonly recovery_references: readonly DeploymentRecoveryReference[];
  readonly reconciles_sequence: number | null;
  readonly system_generated: boolean;
  constructor(values: {
    content: MessageContent;
    sender_label: string;
    mode: PromptDeliveryMode;
    sender_message_id?: string | null;
    sent_at_unix_milliseconds?: number | null;
    sender?: Principal | null;
    recipient?: Principal | null;
    recovery_references?: readonly DeploymentRecoveryReference[];
    reconciles_sequence?: number | null;
    system_generated?: boolean;
  }) {
    this.content = values.content;
    this.sender_label = values.sender_label;
    this.mode = values.mode;
    this.sender_message_id =
      values.sender_message_id === undefined ? null : values.sender_message_id;
    this.sent_at_unix_milliseconds =
      values.sent_at_unix_milliseconds === undefined ? null : values.sent_at_unix_milliseconds;
    this.sender = values.sender === undefined ? null : values.sender;
    this.recipient = values.recipient === undefined ? null : values.recipient;
    this.recovery_references =
      values.recovery_references === undefined ? [] : values.recovery_references;
    this.reconciles_sequence =
      values.reconciles_sequence === undefined ? null : values.reconciles_sequence;
    this.system_generated = values.system_generated === undefined ? false : values.system_generated;
  }
}

export class PromptDeliveryRefusedEventPayload {
  readonly kind = ConversationEventKind.prompt_delivery_refused;
  readonly content: MessageContent;
  readonly sender_label: string;
  readonly mode: PromptDeliveryMode;
  readonly refusal_reason: PromptDeliveryRefusalReason;
  readonly refusal_detail: string | null;
  readonly sender_message_id: string | null;
  readonly sent_at_unix_milliseconds: number | null;
  readonly sender: Principal | null;
  readonly recipient: Principal | null;
  readonly recovery_references: readonly DeploymentRecoveryReference[];
  constructor(values: {
    content: MessageContent;
    sender_label: string;
    mode: PromptDeliveryMode;
    refusal_reason: PromptDeliveryRefusalReason;
    refusal_detail?: string | null;
    sender_message_id?: string | null;
    sent_at_unix_milliseconds?: number | null;
    sender?: Principal | null;
    recipient?: Principal | null;
    recovery_references?: readonly DeploymentRecoveryReference[];
  }) {
    this.content = values.content;
    this.sender_label = values.sender_label;
    this.mode = values.mode;
    this.refusal_reason = values.refusal_reason;
    this.refusal_detail = values.refusal_detail === undefined ? null : values.refusal_detail;
    this.sender_message_id =
      values.sender_message_id === undefined ? null : values.sender_message_id;
    this.sent_at_unix_milliseconds =
      values.sent_at_unix_milliseconds === undefined ? null : values.sent_at_unix_milliseconds;
    this.sender = values.sender === undefined ? null : values.sender;
    this.recipient = values.recipient === undefined ? null : values.recipient;
    this.recovery_references =
      values.recovery_references === undefined ? [] : values.recovery_references;
  }
}

export class PromptDeliveryUncertainEventPayload {
  readonly kind = ConversationEventKind.prompt_delivery_uncertain;
  readonly content: MessageContent;
  readonly sender_label: string;
  readonly mode: PromptDeliveryMode;
  readonly sender_message_id: string | null;
  readonly sent_at_unix_milliseconds: number | null;
  readonly sender: Principal | null;
  readonly recipient: Principal | null;
  readonly recovery_references: readonly DeploymentRecoveryReference[];
  constructor(values: {
    content: MessageContent;
    sender_label: string;
    mode: PromptDeliveryMode;
    sender_message_id?: string | null;
    sent_at_unix_milliseconds?: number | null;
    sender?: Principal | null;
    recipient?: Principal | null;
    recovery_references?: readonly DeploymentRecoveryReference[];
  }) {
    this.content = values.content;
    this.sender_label = values.sender_label;
    this.mode = values.mode;
    this.sender_message_id =
      values.sender_message_id === undefined ? null : values.sender_message_id;
    this.sent_at_unix_milliseconds =
      values.sent_at_unix_milliseconds === undefined ? null : values.sent_at_unix_milliseconds;
    this.sender = values.sender === undefined ? null : values.sender;
    this.recipient = values.recipient === undefined ? null : values.recipient;
    this.recovery_references =
      values.recovery_references === undefined ? [] : values.recovery_references;
  }
}

export class PromptDiscardedEventPayload {
  readonly kind = ConversationEventKind.prompt_discarded;
  readonly content: MessageContent;
  readonly sender_label: string;
  readonly sender_message_id: string | null;
  readonly sent_at_unix_milliseconds: number | null;
  readonly sender: Principal | null;
  readonly recipient: Principal | null;
  readonly recovery_references: readonly DeploymentRecoveryReference[];
  readonly suppression_reason: string | null;
  constructor(values: {
    content: MessageContent;
    sender_label: string;
    sender_message_id?: string | null;
    sent_at_unix_milliseconds?: number | null;
    sender?: Principal | null;
    recipient?: Principal | null;
    recovery_references?: readonly DeploymentRecoveryReference[];
    suppression_reason?: string | null;
  }) {
    this.content = values.content;
    this.sender_label = values.sender_label;
    this.sender_message_id =
      values.sender_message_id === undefined ? null : values.sender_message_id;
    this.sent_at_unix_milliseconds =
      values.sent_at_unix_milliseconds === undefined ? null : values.sent_at_unix_milliseconds;
    this.sender = values.sender === undefined ? null : values.sender;
    this.recipient = values.recipient === undefined ? null : values.recipient;
    this.recovery_references =
      values.recovery_references === undefined ? [] : values.recovery_references;
    this.suppression_reason =
      values.suppression_reason === undefined ? null : values.suppression_reason;
  }
}

export class MessageToOwnerEventPayload {
  readonly kind = ConversationEventKind.message_to_owner;
  readonly content: MessageContent;
  readonly sender: Principal;
  readonly recipient: Principal;
  readonly sender_label: string;
  readonly sender_message_id: string | null;
  readonly sent_at_unix_milliseconds: number | null;
  constructor(values: {
    content: MessageContent;
    sender: Principal;
    recipient: Principal;
    sender_label: string;
    sender_message_id?: string | null;
    sent_at_unix_milliseconds?: number | null;
  }) {
    this.content = values.content;
    this.sender = values.sender;
    this.recipient = values.recipient;
    this.sender_label = values.sender_label;
    this.sender_message_id =
      values.sender_message_id === undefined ? null : values.sender_message_id;
    this.sent_at_unix_milliseconds =
      values.sent_at_unix_milliseconds === undefined ? null : values.sent_at_unix_milliseconds;
  }
}

export class AgentMessageEventPayload {
  readonly kind = ConversationEventKind.agent_message;
  readonly content: MessageContent;
  constructor(values: { content: MessageContent }) {
    this.content = values.content;
  }
}

export class RetiredExplicitReplyMissingEventPayload {
  readonly kind = ConversationEventKind.retired_explicit_reply_missing;
  readonly prompt_sender: Principal;
  constructor(values: { prompt_sender: Principal }) {
    this.prompt_sender = values.prompt_sender;
  }
}

export class ToolCallStartedEventPayload {
  readonly kind = ConversationEventKind.tool_call_started;
  readonly tool_call_id: string;
  readonly title: string;
  readonly tool_kind: string;
  readonly detail: string | null;
  constructor(values: {
    tool_call_id: string;
    title: string;
    tool_kind: string;
    detail: string | null;
  }) {
    this.tool_call_id = values.tool_call_id;
    this.title = values.title;
    this.tool_kind = values.tool_kind;
    this.detail = values.detail;
  }
}

export class ToolCallFinishedEventPayload {
  readonly kind = ConversationEventKind.tool_call_finished;
  readonly tool_call_id: string;
  readonly tool_call_status: ToolCallStatus;
  readonly detail: string | null;
  constructor(values: {
    tool_call_id: string;
    tool_call_status: ToolCallStatus;
    detail: string | null;
  }) {
    this.tool_call_id = values.tool_call_id;
    this.tool_call_status = values.tool_call_status;
    this.detail = values.detail;
  }
}

export class PermissionAskedEventPayload {
  readonly kind = ConversationEventKind.permission_asked;
  readonly ask_id: string;
  readonly title: string;
  readonly detail: string | null;
  readonly options: readonly PermissionAskOption[];
  constructor(values: {
    ask_id: string;
    title: string;
    detail: string | null;
    options: readonly PermissionAskOption[];
  }) {
    this.ask_id = values.ask_id;
    this.title = values.title;
    this.detail = values.detail;
    this.options = values.options;
  }
}

export class PermissionAnsweredEventPayload {
  readonly kind = ConversationEventKind.permission_answered;
  readonly ask_id: string;
  readonly option_id: string;
  readonly fate: AnswerFate;
  readonly answer_id: string | null;
  constructor(values: {
    ask_id: string;
    option_id: string;
    fate?: AnswerFate;
    answer_id?: string | null;
  }) {
    this.ask_id = values.ask_id;
    this.option_id = values.option_id;
    this.fate = values.fate === undefined ? AnswerFate.applied : values.fate;
    this.answer_id = values.answer_id === undefined ? null : values.answer_id;
  }
}

export class AnswerAppliedEventPayload {
  readonly kind = ConversationEventKind.answer_applied;
  readonly answer_id: string;
  constructor(values: { answer_id: string }) {
    this.answer_id = values.answer_id;
  }
}

export class UserInputRequestedEventPayload {
  readonly kind = ConversationEventKind.user_input_requested;
  readonly request_id: string;
  readonly questions: readonly UserInputQuestion[];
  constructor(values: { request_id: string; questions: readonly UserInputQuestion[] }) {
    this.request_id = values.request_id;
    this.questions = values.questions;
  }
}

export class UserInputAnsweredEventPayload {
  readonly kind = ConversationEventKind.user_input_answered;
  readonly request_id: string;
  readonly answers: readonly UserInputAnswer[];
  readonly fate: AnswerFate;
  readonly answer_id: string | null;
  constructor(values: {
    request_id: string;
    answers: readonly UserInputAnswer[];
    fate?: AnswerFate;
    answer_id?: string | null;
  }) {
    this.request_id = values.request_id;
    this.answers = values.answers;
    this.fate = values.fate === undefined ? AnswerFate.applied : values.fate;
    this.answer_id = values.answer_id === undefined ? null : values.answer_id;
  }
}

export class UserInputFailedEventPayload {
  readonly kind = ConversationEventKind.user_input_failed;
  readonly request_id: string;
  readonly detail: string;
  constructor(values: { request_id: string; detail: string }) {
    this.request_id = values.request_id;
    this.detail = values.detail;
  }
}

export class PlanUpdatedEventPayload {
  readonly kind = ConversationEventKind.plan_updated;
  readonly entries: readonly PlanEntry[];
  constructor(values: { entries: readonly PlanEntry[] }) {
    this.entries = values.entries;
  }
}

export class ModelChangedEventPayload {
  readonly kind = ConversationEventKind.model_changed;
  readonly model: string;
  readonly reasoning_effort: string | null;
  constructor(values: { model: string; reasoning_effort: string | null }) {
    this.model = values.model;
    this.reasoning_effort = values.reasoning_effort;
  }
}

export class TokenUsageEventPayload {
  readonly kind = ConversationEventKind.token_usage;
  readonly input_tokens: number | null;
  readonly output_tokens: number | null;
  readonly cached_input_tokens: number | null;
  readonly cost_usd: number | null;
  constructor(values: {
    input_tokens?: number | null;
    output_tokens?: number | null;
    cached_input_tokens?: number | null;
    cost_usd?: number | null;
  }) {
    this.input_tokens = values.input_tokens === undefined ? null : values.input_tokens;
    this.output_tokens = values.output_tokens === undefined ? null : values.output_tokens;
    this.cached_input_tokens =
      values.cached_input_tokens === undefined ? null : values.cached_input_tokens;
    this.cost_usd = values.cost_usd === undefined ? null : values.cost_usd;
  }
}

export class ContextCompactedEventPayload {
  readonly kind = ConversationEventKind.context_compacted;
}

export class CommandReplyEventPayload {
  readonly kind = ConversationEventKind.command_reply;
  readonly reply_text: string;
  constructor(values: { reply_text: string }) {
    this.reply_text = values.reply_text;
  }
}

export class TurnEndedEventPayload {
  readonly kind = ConversationEventKind.turn_ended;
  readonly ending: ConversationTurnEnding;
  readonly error_summary: string | null;
  readonly automatic_compaction_result: AutomaticCompactionResult | null;
  constructor(values: {
    ending: ConversationTurnEnding;
    error_summary?: string | null;
    automatic_compaction_result?: AutomaticCompactionResult | null;
  }) {
    this.ending = values.ending;
    this.error_summary = values.error_summary === undefined ? null : values.error_summary;
    this.automatic_compaction_result =
      values.automatic_compaction_result === undefined ? null : values.automatic_compaction_result;
  }
}

export class CredentialRequestEventPayload {
  readonly kind = ConversationEventKind.credential_request;
  readonly request: CredentialRequestRowBody;
  constructor(values: { request: CredentialRequestRowBody }) {
    this.request = values.request;
  }
}

export class AgentMessageDeltaFrame {
  readonly text_delta: string;
  constructor(values: { text_delta: string }) {
    this.text_delta = values.text_delta;
  }
}

export class ToolCallProgressFrame {
  readonly tool_call_id: string;
  readonly detail: string;
  constructor(values: { tool_call_id: string; detail: string }) {
    this.tool_call_id = values.tool_call_id;
    this.detail = values.detail;
  }
}

export class ModelThinkingFrame {}

export class HeldPromptsChangedFrame {}

export type ConversationEventPayload =
  | CredentialRequestEventPayload
  | PromptEventPayload
  | PromptDeliveryRefusedEventPayload
  | PromptDeliveryUncertainEventPayload
  | PromptDiscardedEventPayload
  | MessageToOwnerEventPayload
  | AgentMessageEventPayload
  | RetiredExplicitReplyMissingEventPayload
  | ToolCallStartedEventPayload
  | ToolCallFinishedEventPayload
  | PermissionAskedEventPayload
  | AnswerAppliedEventPayload
  | PermissionAnsweredEventPayload
  | UserInputRequestedEventPayload
  | UserInputAnsweredEventPayload
  | UserInputFailedEventPayload
  | PlanUpdatedEventPayload
  | ModelChangedEventPayload
  | TokenUsageEventPayload
  | ContextCompactedEventPayload
  | CommandReplyEventPayload
  | TurnEndedEventPayload;

export type ConversationLiveTailFrame =
  AgentMessageDeltaFrame | ToolCallProgressFrame | ModelThinkingFrame | HeldPromptsChangedFrame;

const conversationOnlyKinds: ReadonlySet<ConversationEventKind> = new Set([
  'agent_message',
  'tool_call_started',
  'tool_call_finished',
  'plan_updated',
  'token_usage',
  'context_compacted',
  'command_reply',
]);
export function conversation_event_kinds_need_the_change_signal(
  kinds: Iterable<ConversationEventKind>,
): boolean {
  for (const kind of kinds) if (!conversationOnlyKinds.has(kind)) return true;
  return false;
}
export function conversation_event_payload_kind(
  payload: ConversationEventPayload,
): ConversationEventKind {
  return payload.kind;
}
