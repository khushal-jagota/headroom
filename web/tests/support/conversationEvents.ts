import type {
  ConversationEvent,
  ConversationTurnEnding,
  PermissionAskOption,
  PlanEntry,
  PromptDeliveryMode,
  ToolCallStatus
} from "../../src/lib/conversation/wire";

type EventOf<Kind extends ConversationEvent["kind"]> = Extract<
  ConversationEvent,
  { kind: Kind }
>;
type ConversationEventMetadata = {
  conversationId?: string;
  createdAt?: number;
};
type PromptOverrides = ConversationEventMetadata & {
  senderLabel?: string;
  payload?: Partial<EventOf<"prompt">["payload"]>;
};
type AgentOverrides = ConversationEventMetadata & {
  payload?: Partial<EventOf<"agent_message">["payload"]>;
};
type TurnEndedOptions = ConversationEventMetadata & {
  ending?: ConversationTurnEnding;
  errorSummary?: string | null;
};
type ToolCallStartedOptions = ConversationEventMetadata & {
  toolCallId: string;
  title: string;
  toolKind?: string;
  detail?: string | null;
};
type ToolCallFinishedOptions = ConversationEventMetadata & {
  toolCallId: string;
  status?: ToolCallStatus;
  detail?: string | null;
  /** The server carried only the start of this output. */
  detailCapped?: boolean;
};
type PermissionAskedOptions = ConversationEventMetadata & {
  askId: string;
  title: string;
  options?: PermissionAskOption[];
  detail?: string | null;
};
const defaults = { conversationId: "c1", createdAt: 1_700_000_000 };

function eventMetadata(sequence: number, metadata: ConversationEventMetadata) {
  return {
    conversation_id: metadata.conversationId ?? defaults.conversationId,
    sequence,
    created_at: metadata.createdAt ?? defaults.createdAt
  };
}

export function promptEvent(
  sequence: number,
  text = "hello",
  mode: PromptDeliveryMode = "run_when_free",
  overrides: PromptOverrides = {}
): EventOf<"prompt"> {
  return {
    ...eventMetadata(sequence, overrides),
    kind: "prompt",
    payload: {
      text,
      sender_label: overrides.senderLabel ?? "owner",
      mode,
      ...overrides.payload
    }
  };
}

export function agentMessageEvent(
  sequence: number,
  text: string,
  overrides: AgentOverrides = {}
): EventOf<"agent_message"> {
  return {
    ...eventMetadata(sequence, overrides),
    kind: "agent_message",
    payload: { text, ...overrides.payload }
  };
}

export function explicitReplyMissingEvent(
  sequence: number,
  promptSender: EventOf<"explicit_reply_missing">["payload"]["prompt_sender"],
  metadata: ConversationEventMetadata = {}
): EventOf<"explicit_reply_missing"> {
  return {
    ...eventMetadata(sequence, metadata),
    kind: "explicit_reply_missing",
    payload: { prompt_sender: promptSender }
  };
}

export function turnEndedEvent(
  sequence: number,
  {
    ending = "completed",
    errorSummary = null,
    ...metadata
  }: TurnEndedOptions = {}
): EventOf<"turn_ended"> {
  return {
    ...eventMetadata(sequence, metadata),
    kind: "turn_ended",
    payload: { ending, error_summary: errorSummary }
  };
}

export function toolCallStartedEvent(
  sequence: number,
  { toolCallId, title, toolKind = "read", detail = null, ...metadata }: ToolCallStartedOptions
): EventOf<"tool_call_started"> {
  return {
    ...eventMetadata(sequence, metadata),
    kind: "tool_call_started",
    payload: { tool_call_id: toolCallId, title, tool_kind: toolKind, detail }
  };
}

export function toolCallFinishedEvent(
  sequence: number,
  {
    toolCallId,
    status = "completed",
    detail = null,
    detailCapped = false,
    ...metadata
  }: ToolCallFinishedOptions
): EventOf<"tool_call_finished"> {
  return {
    ...eventMetadata(sequence, metadata),
    kind: "tool_call_finished",
    payload: {
      tool_call_id: toolCallId,
      tool_call_status: status,
      detail,
      ...(detailCapped ? { detail_capped: true } : {})
    }
  };
}

export function planUpdatedEvent(
  sequence: number,
  entries: PlanEntry[],
  overrides: ConversationEventMetadata = {}
): EventOf<"plan_updated"> {
  return {
    ...eventMetadata(sequence, overrides),
    kind: "plan_updated",
    payload: { entries }
  };
}

export function permissionAskedEvent(
  sequence: number,
  { askId, title, options = [], detail = null, ...metadata }: PermissionAskedOptions
): EventOf<"permission_asked"> {
  return {
    ...eventMetadata(sequence, metadata),
    kind: "permission_asked",
    payload: { ask_id: askId, title, detail, options }
  };
}
