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
}>;

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
      state: "held"
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
      state: stateForLocal(message)
    }));
  return [...rows, ...localOnly];
}
