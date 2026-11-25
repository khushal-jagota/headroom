import { rememberAttachmentFile, forgetAttachmentFile, checkpointAttachmentFiles, watchRestoredAttachments, takeRefusedAttachment } from "./updateHandoff";
import { isSafeConversationFileName } from "../pendingFiles";
import { SvelteMap } from "svelte/reactivity";
import { recallDraftVideo, rememberDraftVideo } from "./localSends";
import { MAX_ATTACHMENT_BYTES } from "../attachmentPolicy";
import { measureLocalMedia } from "../../mediaFrame";
import type { PendingAttachment, UploadDestination, AttachmentUploadAdapter } from "./contracts";

/** Owns live transfers only. Completion updates the attachment; it never submits a draft. */
export class AttachmentTransfers {
  private active = new Map<PendingAttachment, AbortController>();
  private validating = new Set<PendingAttachment>();
  private restored: PendingAttachment[] = [];
  private unwatch = watchRestoredAttachments(() => this.restore(this.restored));
  constructor(private adapter: AttachmentUploadAdapter, private changed: () => void) {}
  restore(attachments: PendingAttachment[]): void {
    this.restored = attachments;
    for (const attachment of attachments) {
      const refusal = takeRefusedAttachment(attachment);
      if (refusal) void this.start(attachment, typeof refusal === 'string' ? refusal : undefined);
    }
  }
  create(file: File, id: number, destination: UploadDestination): PendingAttachment {
    if (!isSafeConversationFileName(file.name) || file.size < 0 || file.size > MAX_ATTACHMENT_BYTES)
      throw new Error("Choose a file up to 250 MiB.");
    const attachment: PendingAttachment = { id, fileName: file.name, byteCount: file.size, mediaType: file.type || "application/octet-stream", destination: {...destination}, phase: "uploading", receivedBytes: 0, file,
      ...(/^image\/(png|jpeg|gif|webp)$/.test(file.type) ? { previewUrl: URL.createObjectURL(file) }
        : /^video\//.test(file.type) || /\.(mp4|mov|webm)$/i.test(file.name) ? { localVideoUrl: URL.createObjectURL(file) } : {}) };
    rememberAttachmentFile(attachment);
    checkpointAttachmentFiles();
    return attachment;
  }
  /** Read the picture's or video's size from the local file, before its upload ends. Pass the
   *  attachment as the caller keeps it (its state proxy, if any), so the size is seen. */
  async measure(attachment: PendingAttachment): Promise<void> {
    const url = attachment.previewUrl ?? attachment.localVideoUrl;
    if (!url) return;
    const size = await measureLocalMedia(url, attachment.previewUrl ? "image" : "video");
    if (!size) return;
    attachment.width = size.width;
    attachment.height = size.height;
    this.changed();
  }
  async start(attachment: PendingAttachment, refusedReservation?: string): Promise<void> {
    if (!attachment.file) { attachment.phase = "interrupted"; attachment.error = "Select the file again, then press Send."; this.changed(); return; }
    this.active.get(attachment)?.abort();
    const abort = new AbortController();
    this.active.set(attachment, abort);
    rememberAttachmentFile(attachment);
    const previousId = attachment.uploadId;
    attachment.uploadId = refusedReservation;
    attachment.phase = "uploading"; attachment.error = undefined; attachment.receivedBytes = 0; this.changed();
    if (previousId && !refusedReservation) void this.adapter.cancel(previousId).catch(() => undefined);
    try {
      const reservation = refusedReservation ? {upload_id: refusedReservation} : await this.adapter.reserve(attachment.destination, attachment.file, abort.signal);
      if (abort.signal.aborted) { void this.adapter.cancel(reservation.upload_id).catch(() => undefined); return; }
      attachment.uploadId = reservation.upload_id;
      this.changed();
      const ready = await this.adapter.transfer(reservation.upload_id, attachment.file, abort.signal, bytes => {
        if (abort.signal.aborted) return;
        attachment.receivedBytes = bytes;
        attachment.phase = bytes >= attachment.byteCount ? "validating" : "uploading";
        this.changed();
      });
      if (abort.signal.aborted) return;
      if (ready.state !== "ready") throw new Error(ready.error || "The file is not ready. Retry the upload.");
      attachment.phase = "ready"; attachment.receivedBytes = ready.byte_count;
      attachment.mediaType = ready.media_type || attachment.mediaType;
      attachment.file = undefined;
    } catch (error) {
      if (abort.signal.aborted) return;
      attachment.phase = "failed";
      attachment.error = error instanceof Error ? error.message : "File upload failed.";
    } finally {
      if (this.active.get(attachment) === abort) this.active.delete(attachment);
      checkpointAttachmentFiles();
      this.changed();
    }
  }
  async validate(attachment: PendingAttachment): Promise<void> {
    // A live PUT owns its validating phase. A GET during that phase can still report
    // transferring and must not overwrite the PUT's eventual ready result.
    if (this.active.has(attachment) || this.validating.has(attachment)) return;
    if (!attachment.uploadId) { attachment.phase = "interrupted"; this.changed(); return; }
    this.validating.add(attachment);
    try {
      const status = await this.adapter.status(attachment.uploadId);
      if (status.state !== "ready" || status.destination.expected_conversation_id !== attachment.destination.expected_conversation_id
        || status.destination.kind !== attachment.destination.kind || status.destination.id !== attachment.destination.id)
        throw new Error(status.error || "This file is no longer ready. Select it again.");
      attachment.fileName = status.filename;
      attachment.byteCount = status.byte_count;
      attachment.receivedBytes = status.received_bytes;
      attachment.mediaType = status.media_type || attachment.mediaType;
      attachment.phase = "ready";
      attachment.error = undefined;
    } catch (error) {
      attachment.phase = "failed"; attachment.error = error instanceof Error ? error.message : "Select the file again.";
    } finally { this.validating.delete(attachment); }
    this.changed();
  }
  remove(attachment: PendingAttachment): void {
    forgetAttachmentFile(attachment);
    this.active.get(attachment)?.abort(); this.active.delete(attachment);
    if (attachment.uploadId) void this.adapter.cancel(attachment.uploadId).catch(() => undefined);
    releaseLocalPreview(attachment);
  }
  dispose(): void { this.unwatch(); for (const abort of this.active.values()) abort.abort(); this.active.clear(); }
}

/** Existing saved conversation drafts keep their shape while their transfer accepts every file. */
export { AttachmentTransfers as VideoTransfers };

export { pendingUploadsAsPieces, restoredPendingUploads } from "./pieces";

/** A composer can move under an artifact without creating a second transfer owner. */
export const liveAttachmentDrafts = new SvelteMap<string, PendingAttachment[]>();
export function publishAttachmentDraft(key: string, uploads: PendingAttachment[]): void {
  liveAttachmentDrafts.set(key, uploads);
  for (const attachment of uploads) rememberAttachmentFile(attachment);
  rememberDraftVideo(key, uploads);
}
export function openAttachmentDraft(key: string, initial: PendingAttachment[] = []): PendingAttachment[] {
  const existing = liveAttachmentDrafts.get(key);
  if (existing) return existing;
  const restored = initial.length ? initial : recallDraftVideo(key);
  if (restored.length) publishAttachmentDraft(key, restored);
  return restored;
}
/** Let go of the browser's local copy of a picture or video. A message that was sent keeps
 *  its own reference (localSentMedia), so this runs only for an attachment taken away. */
export function releaseLocalPreview(attachment: PendingAttachment): void {
  if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
  if (attachment.localVideoUrl) URL.revokeObjectURL(attachment.localVideoUrl);
  attachment.previewUrl = undefined;
  attachment.localVideoUrl = undefined;
}
export function clearAttachmentDraft(key: string): void {
  for (const attachment of liveAttachmentDrafts.get(key) ?? []) releaseLocalPreview(attachment);
  publishAttachmentDraft(key, []);
}
const draftTransfers = new Map<string, AttachmentTransfers>();
export function attachmentTransfersFor(key: string, adapter: AttachmentUploadAdapter): AttachmentTransfers {
  let transfers = draftTransfers.get(key);
  if (!transfers) {
    transfers = new AttachmentTransfers(adapter, () => publishAttachmentDraft(key, [...(liveAttachmentDrafts.get(key) ?? [])]));
    draftTransfers.set(key, transfers);
  }
  transfers.restore(liveAttachmentDrafts.get(key) ?? []);
  return transfers;
}
