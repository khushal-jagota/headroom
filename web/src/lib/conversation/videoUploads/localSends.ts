import { releaseOutgoingMessageImages, releaseOutgoingMessageFiles } from "../outgoing";
import type { ComposerSendAttempt } from "../../../components/conversation/composer/draftTransaction";
import type { PromptDeliveryMode, SentMessagePiece } from "../wire";
import type { ComposerSendContext } from "./contracts";
export type PendingComposerSend = {
  context: ComposerSendContext;
  attempt: ComposerSendAttempt;
  mode: PromptDeliveryMode;
  phase: "waiting" | "dispatching" | "failed" | "interrupted";
  error?: string;
  needsReconciliation?: boolean;
};
const PREFIX = "panels:composer-local-sends:";

export function pendingSendContent(entry: PendingComposerSend): SentMessagePiece[] {
  const pieces: SentMessagePiece[] = entry.attempt.content.filter(piece => piece.piece !== "video_upload");
  for (const video of entry.attempt.draftBeforeSend.pendingVideos ?? []) {
    if (video.phase !== "ready" || !video.uploadId) throw new Error("The video is not ready.");
    pieces.push({piece: "video_upload", upload_id: video.uploadId});
  }
  return pieces;
}

/** FIFO includes later text-only sends. A failed upload cannot silently reorder them. */
export class LocalComposerSends {
  entries: PendingComposerSend[] = [];
  private dispatching = false;
  private disposed = false;
  constructor(private dispatch: (entry: PendingComposerSend, content: SentMessagePiece[]) => Promise<boolean>, private changed: () => void) {}
  enqueue(entry: PendingComposerSend): void { this.entries = [...this.entries, entry]; this.changed(); void this.pump(); }
  remove(messageId: string): PendingComposerSend | undefined {
    const entry = this.entries.find(candidate => candidate.context.messageId === messageId);
    if (!entry || entry.phase === "dispatching") return undefined;
    this.entries = this.entries.filter(candidate => candidate !== entry);
    this.changed(); void this.pump(); return entry;
  }
  async pump(): Promise<void> {
    if (this.dispatching || this.disposed) return;
    this.dispatching = true;
    try {
      while (!this.disposed && this.entries.length) {
        const first = this.entries[0];
        if (!first) return;
        if (first.phase !== "waiting") return;
        const videos = first.attempt.draftBeforeSend.pendingVideos ?? [];
        if (videos.some(video => video.phase !== "ready")) return;
        first.phase = "dispatching"; this.changed();
        try {
          const accepted = await this.dispatch(first, pendingSendContent(first));
          if (!accepted) { first.phase = "failed"; first.error = "The message was not sent. Return it to the composer to review."; return; }
          this.entries = this.entries.filter(entry => entry !== first);
        } catch (error) {
          first.phase = "failed"; first.needsReconciliation = true; first.error = error instanceof Error ? error.message : "The message was not sent.";
          return;
        } finally { this.changed(); }
      }
    } finally { this.dispatching = false; }
  }
  dispose(): void { this.disposed = true; }
}

/** Files live only in memory. This serializer retains token metadata and text, never video bytes. */
export function rememberLocalSends(key: string, entries: readonly PendingComposerSend[]): void {
  try {
    if (!entries.length) window.sessionStorage.removeItem(PREFIX + key);
    else window.sessionStorage.setItem(PREFIX + key, JSON.stringify(entries, (key, value) => key === "file" ? undefined : value));
  } catch {
    // A large document/image payload may exceed session storage. Keep the words and
    // video references, with an explicit attachment recovery state, rather than lose text.
    try {
      const compact = entries.map(entry => ({...entry, phase: entry.phase === "dispatching" ? "dispatching" : "interrupted",
        error: "Some attachments could not be saved. Select them again before sending.",
        attempt: {...entry.attempt, content: entry.attempt.content.filter(piece => piece.piece === "text" || piece.piece === "video_upload"),
          draftBeforeSend: {...entry.attempt.draftBeforeSend, pendingImages: [], pendingFiles: [],
            attachmentOrder: entry.attempt.draftBeforeSend.attachmentOrder?.filter(item => item.kind === "video")}}}));
      window.sessionStorage.setItem(PREFIX + key, JSON.stringify(compact, (name, value) => name === "file" ? undefined : value));
    } catch { /* Storage refusal leaves the live state intact. */ }
  }
}
export function recallLocalSends(key: string): PendingComposerSend[] {
  try {
    const entries: unknown = JSON.parse(window.sessionStorage.getItem(PREFIX + key) ?? "[]");
    if (!Array.isArray(entries)) return [];
    return entries.filter((entry): entry is PendingComposerSend => Boolean(entry && typeof entry === "object"
      && typeof entry.context?.messageId === "string" && entry.attempt?.draftBeforeSend
      && typeof entry.attempt.draftBeforeSend.text === "string" && Array.isArray(entry.attempt.content)))
      .map(entry => ({...entry, attempt: {...entry.attempt, draftBeforeSend: {...entry.attempt.draftBeforeSend,
        pendingVideos: (entry.attempt.draftBeforeSend.pendingVideos ?? []).map(video => ({...video,
          phase: video.phase === "ready" ? "ready" : "interrupted", file: undefined,
          error: video.phase === "ready" ? undefined : "Select the video again, then press Send."}))}}, phase: entry.phase === "dispatching" ? "dispatching" : "interrupted",
        needsReconciliation: entry.phase === "dispatching" || entry.needsReconciliation,
        error: "Return this message to the composer, select an interrupted video again, then press Send."}));
  } catch { return []; }
}
export function rememberDraftVideo(key: string, videos: readonly import("./contracts").PendingVideo[]): void {
  try {
    const value = videos.map(({file: _file, ...video}) => video);
    if (!value.length) window.sessionStorage.removeItem(PREFIX + "video:" + key);
    else window.sessionStorage.setItem(PREFIX + "video:" + key, JSON.stringify(value));
  } catch { /* best effort */ }
}
export function recallDraftVideo(key: string): import("./contracts").PendingVideo[] {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(PREFIX + "video:" + key) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter(video => video && typeof video.fileName === "string" && typeof video.destination?.id === "string")
      .map(video => ({...video, phase: video.phase === "ready" ? "validating" : "interrupted", error: "Select the video again, then press Send."}));
  } catch { return []; }
}

/** Remove only the local owner's reservation; the outgoing store retains its own copy. */
export function releaseRemovedLocalSends(previous: readonly PendingComposerSend[], next: readonly PendingComposerSend[]): void {
  const kept = new Set(next.map(entry => entry.context.messageId));
  for (const entry of previous) if (!kept.has(entry.context.messageId)) {
    releaseOutgoingMessageImages(entry.context.messageId);
    releaseOutgoingMessageFiles(entry.context.messageId);
  }
}
