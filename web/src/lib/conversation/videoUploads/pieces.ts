import type { UserInputAttachment } from "../wire";
import type { PendingAttachment, UploadDestination } from "./contracts";
import { mediaSizeOf } from "../../mediaFrame";

export function pendingUploadsAsPieces(uploads: readonly PendingAttachment[]): UserInputAttachment[] {
  return uploads.filter((upload) => upload.phase === "ready" && upload.uploadId).map(uploadPiece);
}

/** One uploaded file as a message piece. A picture or video carries its measured size. */
export function uploadPiece(upload: PendingAttachment): { piece: "attachment_upload"; upload_id: string; width?: number; height?: number } {
  return { piece: "attachment_upload", upload_id: upload.uploadId!, ...(mediaSizeOf(upload) ?? {}) };
}

/** A restored token is checked before a send. The service supplies its original name and size. */
export function restoredPendingUploads(pieces: readonly { piece: string; upload_id?: string }[], destination: UploadDestination, current: readonly PendingAttachment[] = []): PendingAttachment[] {
  const existing = new Set(current.map((attachment) => attachment.uploadId));
  const nextId = Math.max(0, ...current.map((attachment) => attachment.id)) + 1;
  return [...current, ...pieces.filter((piece) => (piece.piece === "attachment_upload" || piece.piece === "video_upload") && piece.upload_id && !existing.has(piece.upload_id))
    .map((piece, id): PendingAttachment => ({ id: id + nextId, fileName: "Attached file", byteCount: 0, mediaType: "application/octet-stream", destination: { ...destination }, uploadId: piece.upload_id, phase: "validating", receivedBytes: 0 }))];
}
