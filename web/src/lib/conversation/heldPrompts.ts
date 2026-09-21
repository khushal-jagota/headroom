/** The rows that belong in the composer queue.
 *
 * The server owns order and actionability. This tab owns messages whose send has not
 * received a complete answer yet. A sender message id joins those two views without
 * allowing an optimistic copy to become a second row.
 */
import {
  messageContentOf,
  messageContentText,
  type HeldPrompt,
  type MessagePiece,
  type PromptQueueReason,
  type SentMessagePiece
} from "./wire";
import type { OutgoingMessage } from "./outgoing";

export type HeldPromptRowState = "held" | "in_flight" | "unknown";

export type HeldPromptRow = Readonly<{
  key: string;
  heldPromptId: string | null;
  senderMessageId: string | null;
  content: readonly (MessagePiece | SentMessagePiece)[];
  senderLabel: string;
  sentAtUnixMilliseconds: number;
  state: HeldPromptRowState;
  queueReason: PromptQueueReason | null;
}>;

/** What can be done with one row in the stack.
 *
 * ``discard``, ``send_now`` and ``steer`` are the record's own operations. Each one names a
 * held prompt the server is holding, so a row the server has never heard of cannot offer
 * them. ``stop_drawing`` and ``send_again`` are the opposite: both are things this tab can
 * do alone, with the words it still has.
 */
export type HeldPromptRowAction =
  | "discard"
  | "send_now"
  | "steer"
  | "stop_drawing"
  | "send_again";

/** The actions this row can offer, before the conversation's own state narrows them.
 *
 * A row the record holds keeps everything it always had. A row that is only this browser's
 * gets something to do only once the wait is over: while a send is still on its way there
 * is nothing to decide, and offering to drop or resend it would invite a person to act on
 * a question that is about to answer itself.
 */
export function heldPromptRowActions(row: HeldPromptRow): readonly HeldPromptRowAction[] {
  if (row.heldPromptId !== null) return ["discard", "send_now", "steer"];
  if (row.state === "unknown") return ["stop_drawing", "send_again"];
  return [];
}

export function heldPromptRowLabel(row: HeldPromptRow): string {
  const words = messageContentText(row.content);
  if (words !== "") return words;
  const imageCount = row.content.filter((piece) => piece.piece === "image").length;
  const fileCount = row.content.filter((piece) => piece.piece === "file").length;
  if (imageCount > 0 && fileCount > 0) {
    return `${imageCount} image${imageCount === 1 ? "" : "s"} · ${fileCount} file${fileCount === 1 ? "" : "s"}`;
  }
  if (fileCount > 0) return fileCount === 1 ? "File message" : `${fileCount} files`;
  return imageCount === 1 ? "Image message" : `${imageCount} images`;
}

function stateForLocal(message: OutgoingMessage): HeldPromptRowState {
  if (message.knownFate === "answer_never_came_back") return "unknown";
  if (message.knownFate === "waiting_for_the_agent") return "held";
  return "in_flight";
}

/** Preserve server FIFO, merge this tab's copies by sender id, then append local sends. */
export function heldPromptRows(
  serverHeldPrompts: readonly HeldPrompt[],
  localMessages: readonly OutgoingMessage[]
): HeldPromptRow[] {
  const localBySenderId = new Map(localMessages.map((message) => [message.messageId, message]));
  const mergedLocalIds = new Set<string>();
  const rows = serverHeldPrompts.map((held): HeldPromptRow => {
    const senderMessageId = held.sender_message_id ?? null;
    const local = senderMessageId === null ? undefined : localBySenderId.get(senderMessageId);
    if (local !== undefined) mergedLocalIds.add(local.messageId);
    return {
      key: `held:${held.held_prompt_id}`,
      heldPromptId: held.held_prompt_id,
      senderMessageId,
      content: local?.content ?? messageContentOf(held),
      senderLabel: held.sender_label,
      sentAtUnixMilliseconds: held.sent_at_unix_milliseconds,
      state: "held",
      queueReason: held.queue_reason
    };
  });
  const localOnly = [...localMessages]
    .filter((message) => !mergedLocalIds.has(message.messageId))
    .sort((one, other) => one.sentAtUnixMilliseconds - other.sentAtUnixMilliseconds)
    .map((message): HeldPromptRow => ({
      key: `local:${message.messageId}`,
      heldPromptId: null,
      senderMessageId: message.messageId,
      content: message.content,
      senderLabel: message.senderLabel,
      sentAtUnixMilliseconds: message.sentAtUnixMilliseconds,
      state: stateForLocal(message),
      queueReason: null
    }));
  return [...rows, ...localOnly];
}
