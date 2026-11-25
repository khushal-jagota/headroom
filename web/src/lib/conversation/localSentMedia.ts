/** The pictures and videos a message carried when this browser sent it.
 *
 * Until Panels stores a picture or video, the only copy is the file in this browser. A
 * sent message draws that copy at once, at its final size, with the upload's progress on
 * top. When the recorded message arrives, the same copy sits under the stored one until
 * the stored one loads, so the change cannot be seen.
 *
 * The record is keyed by the message's sender ID, which the recorded row also carries.
 * It lives in this page only, holds the most recent sends, and never leaves the browser.
 */
import type { PendingAttachment } from "./videoUploads/contracts";
import type { MediaSize } from "../mediaFrame";
import { mediaSizeOf } from "../mediaFrame";

/** A picture or video drawn from the file in this browser. */
export type LocalMediaPiece = {
  piece: "local_media";
  kind: "image" | "video";
  url: string;
  name: string;
  size: MediaSize | null;
  /** Percent uploaded while the upload runs; null once it has finished. */
  progress: number | null;
};

/** Enough recent sends to cover every message still on its way. */
const KEPT_MESSAGES = 32;
const sent = new Map<string, readonly PendingAttachment[]>();

export function rememberSentAttachments(messageId: string, attachments: readonly PendingAttachment[]): void {
  if (!attachments.length) return;
  sent.delete(messageId);
  sent.set(messageId, [...attachments]);
  while (sent.size > KEPT_MESSAGES) sent.delete(sent.keys().next().value!);
}

export function sentAttachmentsOf(messageId: string | null | undefined): readonly PendingAttachment[] {
  return messageId ? sent.get(messageId) ?? [] : [];
}

/** The local picture or video for one attachment, or null for any other file. */
export function localMediaOf(attachment: PendingAttachment): LocalMediaPiece | null {
  const url = attachment.previewUrl ?? attachment.localVideoUrl;
  if (!url) return null;
  return {
    piece: "local_media",
    kind: attachment.previewUrl ? "image" : "video",
    url,
    name: attachment.fileName,
    size: mediaSizeOf(attachment),
    progress: attachment.phase === "uploading" && attachment.byteCount > 0
      ? Math.min(100, Math.floor(attachment.receivedBytes * 100 / attachment.byteCount)) : null
  };
}
