/** Backend children report facts. The core owns delivery and queue semantics. */
import type {
  AnswerFate,
  ComposerCatalogEntry,
  PromptDeliveryMode,
  PromptDeliveryRefusalReason,
  ResolvedConversationStart,
} from '../contracts.ts';
import type {
  ConversationTurnEnding,
  PermissionAskOption,
  PlanEntry,
  ToolCallStatus,
  UserInputAnswer,
  UserInputQuestion,
} from '../events.ts';
import type { MessageContent } from '../message_content.ts';
import type { ConversationMessageFiles } from '../message_files.ts';
export interface TurnToken {
  readonly conversation_id: string;
  readonly turn_number: number;
}
export interface ChildReference {
  readonly executor_incarnation: string;
  readonly child_id: string;
  readonly conversation_id: string;
}
export class BackendAdapterError extends Error {}
export class BackendSpawnFailed extends BackendAdapterError {}
export class SessionLoadFailed extends BackendAdapterError {}
export class PromptWriteRefused extends BackendAdapterError {}
export class PromptWriteFailed extends BackendAdapterError {}
export class RemoteCommandUncertain extends PromptWriteFailed {}
export class DeliveryCancelled extends Error {}
export class BackendPromptRejected extends BackendAdapterError {}
export class PermissionAnswerWriteFailed extends BackendAdapterError {}
export class UserInputAnswerWriteFailed extends BackendAdapterError {}
export class NeedsRebind extends BackendAdapterError {
  readonly failed_child_recovery: boolean;
  constructor(message: string, options: { failed_child_recovery?: boolean } = {}) {
    super(message);
    this.failed_child_recovery = options.failed_child_recovery ?? false;
  }
}
export class BackendPromptAccepted {
  readonly composed_content_delivered: boolean;
  constructor(values: { composed_content_delivered: boolean }) {
    this.composed_content_delivered = values.composed_content_delivered;
  }
}
export class BackendSteerAccepted {
  readonly composed_content_delivered: boolean;
  constructor(values: { composed_content_delivered?: boolean } = {}) {
    this.composed_content_delivered =
      values.composed_content_delivered === undefined ? true : values.composed_content_delivered;
  }
}
export class BackendSteerRefused {
  readonly refusal_reason: PromptDeliveryRefusalReason;
  constructor(values: { refusal_reason: PromptDeliveryRefusalReason }) {
    this.refusal_reason = values.refusal_reason;
  }
}
export class BackendSteerUncertain {
  readonly composed_content_delivered: boolean | null;
  readonly not_delivered: Promise<boolean> | null;
  constructor(
    values: {
      composed_content_delivered?: boolean | null;
      not_delivered?: Promise<boolean> | null;
    } = {},
  ) {
    this.composed_content_delivered =
      values.composed_content_delivered === undefined ? true : values.composed_content_delivered;
    this.not_delivered = values.not_delivered === undefined ? null : values.not_delivered;
  }
}
export class BackendPermissionAsk {
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
export class BackendUserInputRequest {
  readonly request_id: string;
  readonly questions: readonly UserInputQuestion[];
  constructor(values: { request_id: string; questions: readonly UserInputQuestion[] }) {
    this.request_id = values.request_id;
    this.questions = values.questions;
  }
}
export class RetainedBackendRun {
  readonly reference: ChildReference;
  readonly turn: TurnToken;
  readonly delivery_uncertain: boolean;
  readonly retirement_confirmed: boolean;
  readonly stop_requested: boolean;
  readonly incarnation_current: boolean;
  constructor(values: {
    reference: ChildReference;
    turn: TurnToken;
    delivery_uncertain: boolean;
    retirement_confirmed: boolean;
    stop_requested?: boolean;
    incarnation_current?: boolean;
  }) {
    this.reference = values.reference;
    this.turn = values.turn;
    this.delivery_uncertain = values.delivery_uncertain;
    this.retirement_confirmed = values.retirement_confirmed;
    this.stop_requested = values.stop_requested === undefined ? false : values.stop_requested;
    this.incarnation_current =
      values.incarnation_current === undefined ? false : values.incarnation_current;
  }
}
export class AgentMessageDelta {
  readonly text_delta: string;
  constructor(values: { text_delta: string }) {
    this.text_delta = values.text_delta;
  }
}
export class ModelThinkingHappened {
  declare private readonly _ModelThinkingHappened: void;
}
export class AgentMessageCompleted {
  readonly content: MessageContent;
  constructor(values: { content: MessageContent }) {
    this.content = values.content;
  }
}
export class CommandReplyReported {
  readonly reply_text: string;
  constructor(values: { reply_text: string }) {
    this.reply_text = values.reply_text;
  }
}
export class ToolCallStarted {
  readonly tool_call_id: string;
  readonly title: string;
  readonly tool_kind: string;
  readonly detail: string | null;
  constructor(values: {
    tool_call_id: string;
    title: string;
    tool_kind: string;
    detail?: string | null;
  }) {
    this.tool_call_id = values.tool_call_id;
    this.title = values.title;
    this.tool_kind = values.tool_kind;
    this.detail = values.detail === undefined ? null : values.detail;
  }
}
export class ToolCallProgress {
  readonly tool_call_id: string;
  readonly detail: string;
  constructor(values: { tool_call_id: string; detail: string }) {
    this.tool_call_id = values.tool_call_id;
    this.detail = values.detail;
  }
}
export class ToolCallFinished {
  readonly tool_call_id: string;
  readonly tool_call_status: ToolCallStatus;
  readonly detail: string | null;
  constructor(values: {
    tool_call_id: string;
    tool_call_status: ToolCallStatus;
    detail?: string | null;
  }) {
    this.tool_call_id = values.tool_call_id;
    this.tool_call_status = values.tool_call_status;
    this.detail = values.detail === undefined ? null : values.detail;
  }
}
export class PlanUpdated {
  readonly entries: readonly PlanEntry[];
  constructor(values: { entries: readonly PlanEntry[] }) {
    this.entries = values.entries;
  }
}
export class TokenUsageReported {
  readonly input_tokens: number | null;
  readonly output_tokens: number | null;
  readonly cached_input_tokens: number | null;
  readonly cost_usd: number | null;
  constructor(
    values: {
      input_tokens?: number | null;
      output_tokens?: number | null;
      cached_input_tokens?: number | null;
      cost_usd?: number | null;
    } = {},
  ) {
    this.input_tokens = values.input_tokens === undefined ? null : values.input_tokens;
    this.output_tokens = values.output_tokens === undefined ? null : values.output_tokens;
    this.cached_input_tokens =
      values.cached_input_tokens === undefined ? null : values.cached_input_tokens;
    this.cost_usd = values.cost_usd === undefined ? null : values.cost_usd;
  }
}
export class ContextCompacted {
  declare private readonly _ContextCompacted: void;
}
export class PermissionAskRaised {
  readonly ask: BackendPermissionAsk;
  constructor(values: { ask: BackendPermissionAsk }) {
    this.ask = values.ask;
  }
}
export class UserInputRequested {
  readonly request: BackendUserInputRequest;
  constructor(values: { request: BackendUserInputRequest }) {
    this.request = values.request;
  }
}
export class UserInputFailed {
  readonly request_id: string;
  readonly detail: string;
  constructor(values: { request_id: string; detail: string }) {
    this.request_id = values.request_id;
    this.detail = values.detail;
  }
}
export class TurnEnded {
  readonly ending: ConversationTurnEnding;
  readonly error_summary: string | null;
  readonly standard_error_tail: string | null;
  constructor(values: {
    ending: ConversationTurnEnding;
    error_summary?: string | null;
    standard_error_tail?: string | null;
  }) {
    this.ending = values.ending;
    this.error_summary = values.error_summary === undefined ? null : values.error_summary;
    this.standard_error_tail =
      values.standard_error_tail === undefined ? null : values.standard_error_tail;
  }
}
export class VendorSessionCursorRebound {
  readonly vendor_session_cursor: string;
  constructor(values: { vendor_session_cursor: string }) {
    this.vendor_session_cursor = values.vendor_session_cursor;
  }
}
export class ComposerCatalogReported {
  readonly composer_catalog: readonly ComposerCatalogEntry[];
  constructor(values: { composer_catalog: readonly ComposerCatalogEntry[] }) {
    this.composer_catalog = values.composer_catalog;
  }
}
export class BackgroundTaskStarted {
  readonly task_id: string;
  readonly task_description: string;
  constructor(values: { task_id: string; task_description: string }) {
    this.task_id = values.task_id;
    this.task_description = values.task_description;
  }
}
export class BackgroundTaskEnded {
  readonly task_id: string;
  readonly task_status: string;
  constructor(values: { task_id: string; task_status: string }) {
    this.task_id = values.task_id;
    this.task_status = values.task_status;
  }
}
export class BackendStreamEnded {
  declare private readonly _BackendStreamEnded: void;
}

export type BackendSteerOutcome =
  BackendSteerAccepted | BackendSteerRefused | BackendSteerUncertain;
export type BackendEventValue =
  | AgentMessageDelta
  | ModelThinkingHappened
  | AgentMessageCompleted
  | CommandReplyReported
  | ToolCallStarted
  | ToolCallProgress
  | ToolCallFinished
  | PlanUpdated
  | TokenUsageReported
  | ContextCompacted
  | PermissionAskRaised
  | UserInputRequested
  | UserInputFailed
  | TurnEnded
  | VendorSessionCursorRebound
  | ComposerCatalogReported
  | BackgroundTaskStarted
  | BackgroundTaskEnded
  | BackendStreamEnded;
export interface BackendEventSink {
  report(turn: TurnToken | null, event: BackendEventValue): Promise<void>;
}
export interface BackendChild {
  readonly stop_outside_lock: boolean;
  withdraw_delivery(): Promise<boolean>;
  detach(): Promise<void>;
  start(
    resolved_start: ResolvedConversationStart,
    options: { vendor_session_cursor: string | null },
  ): Promise<void>;
  write_prompt(
    turn_token: TurnToken,
    content: MessageContent,
    options: {
      sender_label: string;
      sender_content: MessageContent;
      sender_message_count?: number;
      mode: PromptDeliveryMode;
      model_change: string | null;
      reasoning_effort_change: string | null;
      automatic_compaction?: boolean;
      reasoning_effort_is_set?: boolean;
    },
  ): Promise<BackendPromptAccepted>;
  steer(
    turn_token: TurnToken,
    content: MessageContent,
    options: { sender_content?: MessageContent | null; sender_label: string },
  ): Promise<BackendSteerOutcome>;
  cancel_running_turn(): Promise<void>;
  answer_permission_ask(
    ask_id: string,
    option_id: string,
    options?: { answer_id?: string | null; turn_token?: TurnToken | null },
  ): Promise<AnswerFate>;
  answer_user_input(
    request_id: string,
    answers: readonly UserInputAnswer[],
    options?: { answer_id?: string | null; turn_token?: TurnToken | null },
  ): Promise<AnswerFate>;
  stop(): Promise<void>;
}
export interface BackendChildFactory {
  (options: {
    resolved_start: ResolvedConversationStart;
    event_sink: BackendEventSink;
    message_files: ConversationMessageFiles;
  }): BackendChild;
}
export interface RetainedBackendChildFactory extends BackendChildFactory {
  recover(
    reference: ChildReference,
    options?: { event_sink?: BackendEventSink | null },
  ): BackendChild | null;
  retained_runs(): readonly RetainedBackendRun[];
}
