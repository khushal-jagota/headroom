/** Sending again a message the conversation turned away.
 *
 * A refused message is in the record, words and attachments both: the server kept the
 * files it was sent before it refused the delivery. Sending it again is a new message
 * with the same content, so each kept file is read back from the conversation and goes
 * out with its own bytes, the one shape a sent piece carries.
 *
 * A file the composer could never have sent this way (a video, which travels by upload,
 * or anything over the per-message file size) is not read back: such a message is not
 * offered for sending again rather than being sent without it.
 */
import { conversationFileHref, type MessagePiece, type SentMessagePiece } from "./wire";
import { clientFetch } from "../clientIdentity";
import { ATTACHMENT_READ_LIMIT_MS } from "../requestLimit/config";
import { MAX_CONVERSATION_MESSAGE_FILE_BYTES } from "./pendingFiles";
import { MAX_CONVERSATION_MESSAGE_IMAGE_BYTES } from "./pendingImages";

/** Whether every piece of this recorded message can go out again as it went the first time. */
function recordedPieceCanBeResent(piece: MessagePiece): boolean {
  return piece.piece !== "file"
    || (!/^(video|audio)\//.test(piece.media_type) && piece.byte_count <= MAX_CONVERSATION_MESSAGE_FILE_BYTES);
}

export function recordedContentCanBeResent(content: readonly MessagePiece[]): boolean {
  return content.length > 0 && content.every(recordedPieceCanBeResent);
}

function bytesAsBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunkSize = 0x8000;
  for (let at = 0; at < bytes.length; at += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(at, at + chunkSize));
  }
  return btoa(binary);
}

export const ATTACHMENT_NOT_READ_BACK = "An attachment could not be read back from the conversation, so nothing was sent.";

/** The recorded message as pieces to send: its words as they were, and each kept file
 *  read back from the conversation that kept it. Throws, sending nothing, when any file
 *  cannot be read back whole. */
export async function sentPiecesFromRecorded(
  conversationId: string,
  content: readonly MessagePiece[],
  read: (href: string, init?: RequestInit) => Promise<Response> = (href, init) => clientFetch(href, init, ATTACHMENT_READ_LIMIT_MS)
): Promise<SentMessagePiece[]> {
  if (!recordedContentCanBeResent(content)) throw new Error(ATTACHMENT_NOT_READ_BACK);
  const pieces: SentMessagePiece[] = [];
  for (const piece of content) {
    if (piece.piece === "text") {
      pieces.push({ piece: "text", text: piece.text });
      continue;
    }
    const response = await read(conversationFileHref(conversationId, piece.stored_file_id), { credentials: "same-origin" });
    if (!response.ok) throw new Error(ATTACHMENT_NOT_READ_BACK);
    const bytes = new Uint8Array(await response.arrayBuffer());
    const limit = piece.piece === "image" ? MAX_CONVERSATION_MESSAGE_IMAGE_BYTES : MAX_CONVERSATION_MESSAGE_FILE_BYTES;
    if (bytes.length === 0 || bytes.length > limit) throw new Error(ATTACHMENT_NOT_READ_BACK);
    const data = bytesAsBase64(bytes);
    if (piece.piece === "image") {
      pieces.push({ piece: "image", data, media_type: piece.media_type, ...(piece.file_name ? { file_name: piece.file_name } : {}) });
    } else {
      pieces.push({ piece: "file", data, media_type: piece.media_type, file_name: piece.file_name });
    }
  }
  return pieces;
}

/** Restore everything still available. The failed row keeps any piece that needs reattachment. */
export async function editablePiecesFromRecorded(
  conversationId: string,
  content: readonly MessagePiece[],
  read?: Parameters<typeof sentPiecesFromRecorded>[2]
): Promise<{pieces: SentMessagePiece[]; needsReattachment: boolean}> {
  const pieces: SentMessagePiece[] = [];
  for (const piece of content) {
    if (!recordedPieceCanBeResent(piece)) continue;
    try {
      pieces.push(...await sentPiecesFromRecorded(conversationId, [piece], read));
    } catch {
      // Edit can still recover the other pieces. A send requires every piece.
    }
  }
  return {pieces, needsReattachment: pieces.length !== content.length};
}

/** Given by the live conversation to the messages in it. `available` says whether this
 *  pane can send at all (a past conversation is read only); `resend` sends a recorded
 *  message's content again, as a new message, and resolves true once it got away. */
export type ResendRecordedMessage = {
  available(): boolean;
  /** ``refusedSequence`` is the refused row's sequence: the new prompt names it, and the
   *  refused bubble folds away once that prompt is recorded. */
  edit?(conversationId: string, content: readonly MessagePiece[]): Promise<boolean>;
  editLocal?(id: string): Promise<boolean>;
  resend(conversationId: string, content: readonly MessagePiece[], refusedSequence?: number): Promise<boolean>;
};
export const RESEND_RECORDED_MESSAGE = Symbol("conversation-resend-recorded-message");
