import { messageContentOf, messageContentText, type MessagePiece, type PendingMessage, type SentMessagePiece } from "./wire";
import { queuedMessageLabel, type HeldPromptRow } from "./heldPrompts";
import type { OutgoingMessage } from "./outgoing";
import type { PendingComposerSend } from "./videoUploads/localSends";

export type PendingMessagePresentation =
  | { kind: "held"; row: HeldPromptRow; position: number; count: number }
  | { kind: "upload"; failed: boolean; note: string; files: readonly {
      name: string; progress: number | null; note: string; failed: boolean;
    }[] };

export const TRANSCRIPT_SEND_ACTIONS = Symbol("transcript-send-actions");
export type TranscriptSendActions = {
  running(): boolean;
  canSteer(): boolean;
  discard(row: HeldPromptRow): Promise<unknown> | void;
  promote(row: HeldPromptRow, mode: "send_now" | "steer"): Promise<unknown> | void;
  editHeld(row: HeldPromptRow, words: string): Promise<unknown> | void;
  canEditHeld(): boolean;
  editUpload?(id: string): Promise<unknown> | void;
  retryUpload?(id: string): Promise<unknown> | void;
};

export function editableHeldWords(row: HeldPromptRow): string | null {
  return row.heldPromptId && row.content.every(piece => piece.piece === "text")
    ? messageContentText(row.content).trim() || null : null;
}

export function displayedOutgoingContent(local: OutgoingMessage): readonly (MessagePiece | SentMessagePiece)[] {
  return local.presentation?.kind === "held" ? local.presentation.row.content : local.content;
}

/** Accepted messages the conversation has not placed yet, as rows accepted for delivery.
 *  The sender ID joins each to its prompt row, which replaces it, and to this tab's own copy. */
export function acceptedPendingMessages(
  pending: readonly PendingMessage[],
  committed: ReadonlySet<string>,
  local: readonly OutgoingMessage[]
): OutgoingMessage[] {
  // A message without its own instant keeps the server's order, after everything before it.
  let latest = Math.max(0, ...local.map(own => own.sentAtUnixMilliseconds));
  return pending
    .filter(message => !committed.has(message.sender_message_id)
      && !local.some(own => own.messageId === message.sender_message_id))
    .map(message => {
      const pieces = messageContentOf(message);
      const attached = pieces.filter((piece): piece is Exclude<MessagePiece, { piece: "text" }> => piece.piece !== "text");
      latest = Math.max(latest, message.sent_at_unix_milliseconds ?? latest);
      return {
        messageId: message.sender_message_id,
        content: pieces.flatMap(piece => piece.piece === "text" ? [piece] : []),
        // Its files stay at home until delivery. The row names them, as a send on its way does.
        ...(attached.length ? { attachmentNames: attached.map(piece => piece.file_name ?? queuedMessageLabel([piece])) } : {}),
        senderLabel: message.sender_label,
        mode: message.requested_mode,
        sentAtUnixMilliseconds: message.sent_at_unix_milliseconds ?? latest,
        restored: true,
        knownFate: "accepted_for_delivery" as const
      };
    });
}

/** Upload, transport and server custody all use the same sender ID and transcript slot. */
export function transcriptOutgoing(
  outgoing: readonly OutgoingMessage[],
  uploading: readonly PendingComposerSend[],
  held: readonly HeldPromptRow[],
  ownerLabel: string,
  acknowledged: ReadonlySet<string>
): OutgoingMessage[] {
  const messages = new Map(outgoing.map(message => [message.messageId, message]));
  for (const entry of uploading) {
    const messageId = entry.context.messageId;
    if (acknowledged.has(messageId)) continue;
    const existing = messages.get(messageId);
    if (existing && (existing.accepted || existing.knownFate !== "nothing_yet")) continue;
    const files = (entry.attempt.draftBeforeSend.pendingVideos ?? []).map(file => ({
      name: file.fileName,
      progress: file.phase === "uploading" && file.byteCount > 0
        ? Math.min(100, Math.floor(file.receivedBytes * 100 / file.byteCount)) : null,
      note: file.phase === "ready" ? "Uploaded"
        : file.phase === "failed" || file.phase === "interrupted" ? file.error ?? "Attach again"
        : file.phase === "validating" ? "Checking file…" : "Uploading…",
      failed: file.phase === "failed" || file.phase === "interrupted"
    }));
    const failed = entry.phase === "failed" || entry.phase === "interrupted"
      || (entry.attempt.draftBeforeSend.pendingVideos ?? []).some(file => file.phase === "failed" || file.phase === "interrupted");
    messages.set(messageId, {
      ...existing,
      messageId,
      content: [...entry.attempt.content],
      senderLabel: ownerLabel,
      mode: entry.mode,
      queueWhilePending: entry.context.queueWhilePending,
      sentAtUnixMilliseconds: entry.sentAtUnixMilliseconds ?? existing?.sentAtUnixMilliseconds ?? 0,
      restored: entry.restored || entry.sentAtUnixMilliseconds === undefined || existing?.restored,
      knownFate: failed ? "not_sent" : "nothing_yet",
      presentation: {kind: "upload", files, failed, note: entry.error ?? (
        failed ? "An attachment could not be uploaded."
          : files.some(file => file.note !== "Uploaded") ? "Uploading…" : "Sending…"
      )}
    });
  }
  const queued = held.filter(row => row.state === "held");
  for (const row of held) {
    const messageId = row.senderMessageId ?? row.key;
    if (acknowledged.has(messageId)) continue;
    const existing = messages.get(messageId);
    if (existing && (existing.accepted || existing.knownFate === "agent_unconfirmed"
      || existing.knownFate === "not_sent" || existing.knownFate === "machine_offline")) continue;
    messages.set(messageId, {
      ...existing,
      messageId,
      content: existing?.content ?? [],
      senderLabel: row.senderLabel,
      mode: row.mode ?? existing?.mode ?? "queue",
      sentAtUnixMilliseconds: row.sentAtUnixMilliseconds,
      restored: existing?.restored ?? !existing,
      knownFate: row.state === "unknown" ? "agent_unconfirmed" : "waiting_for_the_agent",
      presentation: {kind: "held", row, position: queued.indexOf(row) + 1, count: queued.length}
    });
  }
  return [...messages.values()].sort((left, right) => left.sentAtUnixMilliseconds - right.sentAtUnixMilliseconds);
}

/** A server-held queue row, or a Queue send behind a running turn that the server has not
 *  placed yet: on its way, or accepted and not yet held. It stays in the tray meanwhile. */
export function belongsInHeldQueue(message: OutgoingMessage): boolean {
  if (message.mode !== "queue" || message.accepted) return false;
  if (message.knownFate === "waiting_for_the_agent") return true;
  return message.queueWhilePending === true
    && (message.knownFate === "nothing_yet" || message.knownFate === "accepted_for_delivery");
}

export function heldQueuePosition(message: OutgoingMessage): number {
  return message.presentation?.kind === "held" ? message.presentation.position : Number.MAX_SAFE_INTEGER;
}
