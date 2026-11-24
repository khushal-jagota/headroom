import { messageContentText, type MessagePiece, type SentMessagePiece } from "./wire";
import type { HeldPromptRow } from "./heldPrompts";
import type { OutgoingMessage } from "./outgoing";
import type { PendingComposerSend } from "./videoUploads/localSends";

export type PendingMessagePresentation =
  | { kind: "held"; row: HeldPromptRow; position: number; count: number }
  | { kind: "upload"; failed: boolean; note: string; files: readonly {
      name: string; progress: number | null; note: string;
    }[] };

export const TRANSCRIPT_SEND_ACTIONS = Symbol("transcript-send-actions");
export type TranscriptSendActions = {
  running(): boolean;
  canSteer(): boolean;
  discard(row: HeldPromptRow): Promise<unknown> | void;
  promote(row: HeldPromptRow, mode: "send_now" | "steer"): Promise<unknown> | void;
  editHeld(row: HeldPromptRow, words: string): Promise<unknown> | void;
  canEditHeld(): boolean;
  editUpload(id: string): Promise<unknown> | void;
  retryUpload(id: string): Promise<unknown> | void;
};

export function editableHeldWords(row: HeldPromptRow): string | null {
  return row.heldPromptId && row.content.every(piece => piece.piece === "text")
    ? messageContentText(row.content).trim() || null : null;
}

export function displayedOutgoingContent(local: OutgoingMessage): readonly (MessagePiece | SentMessagePiece)[] {
  return local.presentation?.kind === "held" ? local.presentation.row.content : local.content;
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
        : file.phase === "validating" ? "Checking file…" : "Uploading…"
    }));
    const failed = entry.phase === "failed" || entry.phase === "interrupted"
      || (entry.attempt.draftBeforeSend.pendingVideos ?? []).some(file => file.phase === "failed" || file.phase === "interrupted");
    messages.set(messageId, {
      ...existing,
      messageId,
      content: [...entry.attempt.content],
      senderLabel: ownerLabel,
      mode: entry.mode,
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
      mode: "queue",
      sentAtUnixMilliseconds: row.sentAtUnixMilliseconds,
      restored: existing?.restored ?? !existing,
      knownFate: row.state === "unknown" ? "agent_unconfirmed" : "waiting_for_the_agent",
      presentation: {kind: "held", row, position: queued.indexOf(row) + 1, count: queued.length}
    });
  }
  return [...messages.values()].sort((left, right) => left.sentAtUnixMilliseconds - right.sentAtUnixMilliseconds);
}
