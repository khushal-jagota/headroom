import { isSafeConversationFileName } from "../pendingFiles";
import { isVideoFile, MAX_VIDEO_BYTES } from "./config";
import type { PendingVideo, UploadDestination, VideoUploadAdapter } from "./contracts";

/** Owns live transfers only. Completion updates the attachment; it never submits text. */
export class VideoTransfers {
  private active = new Map<PendingVideo, AbortController>();
  constructor(private adapter: VideoUploadAdapter, private changed: () => void) {}
  create(file: File, id: number, destination: UploadDestination): PendingVideo {
    if (!isVideoFile(file.name) || !isSafeConversationFileName(file.name) || file.size <= 0 || file.size > MAX_VIDEO_BYTES)
      throw new Error("Choose one MP4, MOV or WebM video up to 250 MiB.");
    return { id, fileName: file.name, byteCount: file.size, mediaType: file.type || "video/mp4", destination: {...destination}, phase: "uploading", receivedBytes: 0, file };
  }
  async start(video: PendingVideo): Promise<void> {
    if (!video.file) { video.phase = "interrupted"; video.error = "Select the video again, then press Send."; this.changed(); return; }
    this.active.get(video)?.abort();
    const abort = new AbortController();
    this.active.set(video, abort);
    const previousId = video.uploadId;
    video.uploadId = undefined;
    video.phase = "uploading"; video.error = undefined; video.receivedBytes = 0; this.changed();
    if (previousId) void this.adapter.cancel(previousId).catch(() => undefined);
    try {
      const reservation = await this.adapter.reserve(video.destination, video.file, abort.signal);
      if (abort.signal.aborted) { void this.adapter.cancel(reservation.upload_id).catch(() => undefined); return; }
      video.uploadId = reservation.upload_id;
      this.changed();
      const ready = await this.adapter.transfer(reservation.upload_id, video.file, abort.signal, bytes => {
        if (abort.signal.aborted) return;
        video.receivedBytes = bytes;
        video.phase = bytes >= video.byteCount ? "validating" : "uploading";
        this.changed();
      });
      if (abort.signal.aborted) return;
      if (ready.state !== "ready") throw new Error(ready.error || "The video is not ready. Retry the upload.");
      video.phase = "ready"; video.receivedBytes = ready.byte_count;
      video.mediaType = ready.media_type || video.mediaType;
    } catch (error) {
      if (abort.signal.aborted) return;
      video.phase = "failed";
      video.error = error instanceof Error ? error.message : "Video upload failed.";
    } finally {
      if (this.active.get(video) === abort) this.active.delete(video);
      this.changed();
    }
  }
  async validate(video: PendingVideo): Promise<void> {
    if (!video.uploadId) { video.phase = "interrupted"; return; }
    try {
      const status = await this.adapter.status(video.uploadId);
      if (status.state !== "ready" || status.destination.expected_conversation_id !== video.destination.expected_conversation_id
        || status.destination.kind !== video.destination.kind || status.destination.id !== video.destination.id)
        throw new Error(status.error || "This video is no longer ready. Select it again.");
      video.phase = "ready";
    } catch (error) {
      video.phase = "failed"; video.error = error instanceof Error ? error.message : "Select the video again.";
    }
    this.changed();
  }
  remove(video: PendingVideo): void {
    this.active.get(video)?.abort(); this.active.delete(video);
    if (video.uploadId) void this.adapter.cancel(video.uploadId).catch(() => undefined);
  }
  dispose(): void { for (const abort of this.active.values()) abort.abort(); this.active.clear(); }
}
