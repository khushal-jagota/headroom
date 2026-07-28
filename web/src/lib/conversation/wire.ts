/** The conversation system's HTTP wire, as the browser speaks it.
 *
 * Every shape here is exactly what `/api/conversation` serializes, and every call is a
 * thin one: it asks, it reads what came back, and it hands it over. Nothing here decides
 * anything — a refusal is a fate the caller renders, not an error thrown at it, because
 * the server already said a refused delivery is an answer rather than a broken request.
 *
 * Reading a conversation is two doors that fit together, so this module offers exactly
 * those two: the rows after a position, and a tail that replays those same rows and then
 * keeps going. Opening, reloading, and opening a second tab are all the same act.
 */

export const CONVERSATION_BASE = "/api/conversation";

/** The two things a tail carries, named so a reader never has to guess which it holds. */
export const COMMITTED_EVENT_STREAM_NAME = "conversation-event";
export const LIVE_FRAME_STREAM_NAME = "conversation-frame";

export type ConversationBackendKey = "hermes" | "codex" | "claude";

/** The production catalog, in the order the server lists it. */
export const CONVERSATION_BACKEND_KEYS: readonly ConversationBackendKey[] = [
  "hermes",
  "codex",
  "claude"
];

/** Steering is a per-backend fact the server states, not a runtime negotiation. */
export const BACKEND_KEYS_SUPPORTING_STEER: readonly ConversationBackendKey[] = ["hermes"];

export function backendSupportsSteer(backendKey: ConversationBackendKey | null): boolean {
  return backendKey !== null && BACKEND_KEYS_SUPPORTING_STEER.includes(backendKey);
}

export type PromptDeliveryMode = "run_when_free" | "send_now" | "steer";

export type ConversationTurnEnding = "completed" | "failed" | "interrupted";

export type ToolCallStatus = "completed" | "failed";

export type PromptDeliveryRefusalReason =
  | "no_such_conversation"
  | "backend_did_not_start"
  | "session_did_not_load"
  | "write_to_backend_failed"
  | "no_running_turn_to_steer_into"
  | "backend_cannot_steer";

/** What one message is made of: written words, and pictures.
 *
 * Nearly every message is one piece of written words, and that is what a message with
 * only words is: a run of one. A picture names a file the record kept, which
 * `conversationFileHref` turns into somewhere to fetch it from.
 */
export type MessagePiece =
  | { piece: "text"; text: string }
  | { piece: "image"; stored_file_id: string; media_type: string; file_name?: string };

export type MessageContent = MessagePiece[];

/** A message payload as the record stores it, which is one of two shapes.
 *
 * A message that is only words is stored under `text`, exactly as every message was
 * before a message could be anything else — so nothing already recorded has to be
 * rewritten and an ordinary row never grows. Everything else is stored under `content`.
 */
export type StoredMessageContent = { text: string } | { content: MessagePiece[] };

/** Either stored shape, read as the pieces the message is made of.
 *
 * The one place in this browser that knows there are two shapes. Everything above it
 * holds a run of pieces and never asks which form the row was in.
 */
export function messageContentOf(payload: StoredMessageContent): MessagePiece[] {
  if ("content" in payload) return payload.content;
  return [{ piece: "text", text: payload.text }];
}

/** Everything a message says in words, for the places that can only hold words.
 *
 * The pieces that are not words are left out rather than described: a stand-in sentence
 * would read as something the sender wrote.
 */
export function messageContentText(
  content: readonly (MessagePiece | SentMessagePiece)[]
): string {
  return content
    .filter(
      (piece): piece is Extract<MessagePiece, { piece: "text" }> => piece.piece === "text"
    )
    .map((piece) => piece.text)
    .join("\n\n");
}

/** Where to fetch a file one of this conversation's messages carries. */
export function conversationFileHref(conversationId: string, storedFileId: string): string {
  return `${CONVERSATION_BASE}/conversations/${encodeURIComponent(conversationId)}`
    + `/files/${encodeURIComponent(storedFileId)}`;
}

/** Where one step of the agent's plan has got to. */
export type PlanEntryStatus = "pending" | "in_progress" | "completed";

export type PlanEntry = { text: string; status: PlanEntryStatus };

export type PermissionAskOption = {
  option_id: string;
  label: string;
  option_kind: string;
};

export type PendingPermissionAsk = {
  ask_id: string;
  title: string;
  detail: string | null;
  options: PermissionAskOption[];
};

/**
 * One command the agent says a person may type at it. The name carries no leading slash
 * — the slash is how a person writes a command, not part of what it is called.
 */
export type AgentCommand = {
  name: string;
  description: string;
  argument_hint: string | null;
};

/** What a conversation is, what it is doing, and what it is waiting on. */
export type ConversationView = {
  conversation_id: string;
  backend_key: ConversationBackendKey;
  model: string | null;
  reasoning_effort: string | null;
  workspace_folder: string;
  access: string;
  role_text: string | null;
  identity_environment_variable_names: string[];
  latest_sequence: number;
  is_running: boolean;
  held_prompt_count: number;
  pending_permission_ask: PendingPermissionAsk | null;
  available_commands: AgentCommand[];
};

type Row<Kind extends string, Payload> = {
  conversation_id: string;
  sequence: number;
  kind: Kind;
  payload: Payload;
  created_at: number;
};

/** The sender's own two facts about a message it sent.
 *
 * The id is how a sender recognises its own message when the record hands it back: this
 * browser draws a message the moment Enter is pressed, and the id is what says that the
 * copy it drew and this row are the same message. The instant is when the person pressed
 * send, in unix milliseconds by this browser's clock — which is where a turn's clock
 * starts, and is not the same thing as ``created_at``, the whole second the row was
 * written in. Both are absent when nobody minted them — a message sent from anywhere
 * other than a browser has neither, and every row already in a record predates them. */
type SenderMintedPromptFields = {
  sender_message_id?: string;
  sent_at_unix_milliseconds?: number;
};

export type ConversationEvent =
  | Row<
      "prompt",
      StoredMessageContent & {
        sender_label: string;
        mode: PromptDeliveryMode;
      } & SenderMintedPromptFields
    >
  | Row<
      "prompt_delivery_refused",
      StoredMessageContent & {
        sender_label: string;
        mode: PromptDeliveryMode;
        refusal_reason: PromptDeliveryRefusalReason;
        sender_message_id?: string;
      }
    >
  | Row<
      "prompt_discarded",
      StoredMessageContent & { sender_label: string; sender_message_id?: string }
    >
  | Row<"agent_message", StoredMessageContent>
  | Row<
      "tool_call_started",
      { tool_call_id: string; title: string; tool_kind: string; detail: string | null }
    >
  | Row<
      "tool_call_finished",
      { tool_call_id: string; tool_call_status: ToolCallStatus; detail: string | null }
    >
  | Row<
      "permission_asked",
      { ask_id: string; title: string; detail: string | null; options: PermissionAskOption[] }
    >
  | Row<"permission_answered", { ask_id: string; option_id: string }>
  | Row<"model_changed", { model: string | null; reasoning_effort: string | null }>
  /** What a turn has cost, as its backend counts it. Every field is absent when the
   *  backend did not say — never zero, because a backend silent about cached tokens has
   *  not said there were none. Only claude reports money. */
  | Row<
      "token_usage",
      {
        input_tokens?: number;
        output_tokens?: number;
        cached_input_tokens?: number;
        cost_usd?: number;
      }
    >
  /** The backend summarised what came before and dropped it. No payload: that it
   *  happened, and where, is the whole of it. */
  | Row<"context_compacted", Record<string, never>>
  /** The agent's plan as it stands now. Each row is the whole plan, not a change to it,
   *  so the newest one is the plan and the ones before it are history. */
  | Row<"plan_updated", { entries: PlanEntry[] }>
  | Row<"turn_ended", { ending: ConversationTurnEnding; error_summary: string | null }>;

export type ConversationEventKind = ConversationEvent["kind"];

/** Half-finished output. Shown and then forgotten; never a row.
 *
 * ``model_thinking`` carries nothing on purpose. Private reasoning is not stored and is
 * not shown, so the only thing this frame says is that the model was thinking just now —
 * which is enough to tell a person the silence is not a stall. A backend may send none of
 * these on a turn, or none ever, and the pane reads the same either way.
 */
export type ConversationLiveFrame =
  | { frame: "agent_message_delta"; text_delta: string }
  | { frame: "tool_call_progress"; tool_call_id: string; detail: string }
  | { frame: "model_thinking" };

export type PromptDeliveryFate =
  | { fate: "started" }
  | { fate: "queued"; queue_position: number }
  | { fate: "injected" }
  | { fate: "refused"; refusal_reason: PromptDeliveryRefusalReason };

export type BackendIdentity = {
  status: "authenticated" | "unauthenticated" | "unknown";
  account_label: string | null;
  detail: string | null;
  login_command: string | null;
};

export type BackendModel = {
  model_id: string;
  display_name: string | null;
  /** What this model really is, when the name alone does not say — an alias and the
   *  version it reaches, for instance. Optional: a catalog that offers none is read the
   *  same way as one that has not started offering them yet. */
  detail?: string | null;
  /** The reasoning efforts this particular model takes, where they differ per model
   *  rather than per backend. Absent means the backend's own list is the answer; an
   *  empty list means this model takes none, and no effort control exists for it. */
  reasoning_effort_options?: string[];
};

export type BackendUpdateAdvisory = {
  install_method: string;
  update_command: string | null;
  latest_version: string | null;
  update_available: boolean;
  detail: string;
};

export type BackendSnapshot = {
  backend_key: ConversationBackendKey;
  installed: boolean;
  executable_path: string | null;
  version: string | null;
  identity: BackendIdentity | null;
  available_models: BackendModel[];
  reasoning_effort_options: string[];
  /** The concrete model this backend runs when nobody names one. Optional: a catalog
   *  that has not said yet is read as absence, never as a word called "default". */
  default_model_id?: string | null;
  /** The same for reasoning effort. Some backends genuinely name none. */
  default_reasoning_effort?: string | null;
  update_advisory: BackendUpdateAdvisory | null;
  diagnoses: string[];
};

export type BackendUpdateResult = {
  outcome: "succeeded" | "unchanged" | "failed";
  detail: string;
  output_tail: string;
};

export type StartConversationBody = {
  conversation_id: string;
  backend_key?: ConversationBackendKey;
  model?: string | null;
  reasoning_effort?: string | null;
  workspace_folder?: string;
};

/** A piece as it is sent, which is the one shape that carries bytes.
 *
 * A picture goes out with its own bytes, base64, riding with the message it belongs to.
 * The server keeps them and the record names what it kept, so `data` is written here and
 * is never read back: what comes back names a file instead.
 */
export type SentMessagePiece =
  | { piece: "text"; text: string }
  | { piece: "image"; data: string; media_type: string; file_name?: string };

export type SendPromptBody = {
  content: SentMessagePiece[];
  sender_label: string;
  mode: PromptDeliveryMode;
  model_change?: string;
  reasoning_effort_change?: string;
} & SenderMintedPromptFields;

/** A message to an owner — a Ticket's worker, or the Chief — rather than to a conversation.
 *
 * `conversation_id` is which conversation this is for, and absent says the sender has none:
 * this message is what brings one into being. The three values say what it runs under, and
 * to a conversation that does not exist yet they are what to create it on.
 */
export type OwnerSendBody = {
  conversation_id: string | null;
  backend_key?: string;
  model?: string;
  reasoning_effort?: string;
  content: SentMessagePiece[];
  sender_label: string;
  mode: PromptDeliveryMode;
} & SenderMintedPromptFields;

/** What happened to a message, and which conversation it happened in.
 *
 * The id is null when a message that was to make a conversation did not land: there is no
 * conversation then, so there is nothing to open and nothing to hold on to.
 */
export type DeliveredMessage = PromptDeliveryFate & { conversation_id: string | null };

/** A request the server answered with a refusal of the request itself.
 *
 * The conversation routes raise FastAPI's own errors, so what comes back is a `detail`
 * sentence rather than the planner error envelope. That sentence is the whole message
 * the server meant to send, so it is what this carries.
 */
export class ConversationWireError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ConversationWireError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${CONVERSATION_BASE}${path}`, init);
  } catch {
    throw new ConversationWireError("The server could not be reached.", 0);
  }
  const raw = await response.text();
  if (response.ok) {
    if (raw === "") return {} as T;
    try {
      return JSON.parse(raw) as T;
    } catch {
      throw new ConversationWireError("The server sent something that is not JSON.", response.status);
    }
  }
  throw new ConversationWireError(detailSentence(raw, response.status), response.status);
}

function detailSentence(raw: string, status: number): string {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string" && detail !== "") return detail;
    }
  } catch {
    // Not JSON. The status is all the server said.
  }
  return `The server answered ${status}.`;
}

function postJson(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  };
}

export function startConversation(body: StartConversationBody): Promise<ConversationView> {
  return request<ConversationView>("/conversations", postJson(body));
}

export function readConversation(conversationId: string): Promise<ConversationView> {
  return request<ConversationView>(`/conversations/${encodeURIComponent(conversationId)}`);
}

export async function readEventsAfter(
  conversationId: string,
  after: number
): Promise<ConversationEvent[]> {
  const answer = await request<{ events: ConversationEvent[] }>(
    `/conversations/${encodeURIComponent(conversationId)}/events?after=${after}`
  );
  return answer.events;
}

export function sendPrompt(
  conversationId: string,
  body: SendPromptBody
): Promise<PromptDeliveryFate> {
  return request<PromptDeliveryFate>(
    `/conversations/${encodeURIComponent(conversationId)}/send`,
    postJson(body)
  );
}

export async function interruptConversation(conversationId: string): Promise<void> {
  await request<Record<string, never>>(
    `/conversations/${encodeURIComponent(conversationId)}/interrupt`,
    { method: "POST" }
  );
}

export async function killConversation(conversationId: string): Promise<void> {
  await request<Record<string, never>>(
    `/conversations/${encodeURIComponent(conversationId)}/kill`,
    { method: "POST" }
  );
}

export function answerPermissionAsk(
  conversationId: string,
  askId: string,
  optionId: string
): Promise<{ landed: boolean }> {
  return request<{ landed: boolean }>(
    `/conversations/${encodeURIComponent(conversationId)}/permission-answers`,
    postJson({ ask_id: askId, option_id: optionId })
  );
}

/** Throw away one message that is waiting, by the name this browser gave it.
 *
 * A false is an ordinary answer: a held message runs the moment the agent frees up, so
 * the one being cancelled may already have gone.
 */
export function discardHeldPrompt(
  conversationId: string,
  senderMessageId: string
): Promise<{ discarded: boolean }> {
  return request<{ discarded: boolean }>(
    `/conversations/${encodeURIComponent(conversationId)}`
      + `/held-prompts/${encodeURIComponent(senderMessageId)}`,
    { method: "DELETE" }
  );
}

export async function readBackends(refresh = false): Promise<BackendSnapshot[]> {
  const answer = await request<{ backends: BackendSnapshot[] }>(
    `/backends${refresh ? "?refresh=true" : ""}`
  );
  return answer.backends;
}

export function updateBackend(
  backendKey: ConversationBackendKey
): Promise<BackendUpdateResult> {
  return request<BackendUpdateResult>(`/backends/${backendKey}/update`, { method: "POST" });
}

export type ConversationTailHandlers = {
  onCommittedEvent: (event: ConversationEvent) => void;
  onLiveFrame: (frame: ConversationLiveFrame) => void;
  onTrouble: () => void;
};

/** Open the live tail from a position and return the way to close it.
 *
 * The browser's own EventSource would reconnect by itself from the position this tail
 * opened at, which would replay the whole conversation over again. So trouble closes
 * this one and is reported: reconnecting is a fetch from where the reader actually got
 * to, followed by a fresh tail, and that is the caller's business rather than this one's.
 */
export function openConversationTail(
  conversationId: string,
  after: number,
  handlers: ConversationTailHandlers
): () => void {
  const source = new EventSource(
    `${CONVERSATION_BASE}/conversations/${encodeURIComponent(conversationId)}/tail?after=${after}`
  );
  let closed = false;
  const close = (): void => {
    if (closed) return;
    closed = true;
    source.close();
  };
  source.addEventListener(COMMITTED_EVENT_STREAM_NAME, (message: MessageEvent<string>) => {
    handlers.onCommittedEvent(JSON.parse(message.data) as ConversationEvent);
  });
  source.addEventListener(LIVE_FRAME_STREAM_NAME, (message: MessageEvent<string>) => {
    handlers.onLiveFrame(JSON.parse(message.data) as ConversationLiveFrame);
  });
  source.addEventListener("error", () => {
    close();
    handlers.onTrouble();
  });
  return close;
}
