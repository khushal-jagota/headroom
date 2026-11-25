import { hydrateAttachmentFile, wasRefusedAttachmentResumed } from "./updateHandoff";
import { releaseOutgoingMessageImages, releaseOutgoingMessageFiles } from "../outgoing";
import type { ComposerSendAttempt } from "../../../components/conversation/composer/draftTransaction";
import type { PromptDeliveryMode, SentMessagePiece } from "../wire";
import type { ComposerSendContext } from "./contracts";
import { uploadPiece } from "./pieces";
export type PendingComposerSend = {
  context: ComposerSendContext;
  attempt: ComposerSendAttempt;
  mode: PromptDeliveryMode;
  sentAtUnixMilliseconds?: number;
  restored?: boolean;
  phase: "waiting" | "dispatching" | "failed" | "interrupted";
  error?: string;
  needsReconciliation?: boolean;
  needsReattachment?: boolean;
  /** Captured at an update handoff while this requested message was still unsent. */
  resumeAfterUpdate?: boolean;
};
const PREFIX = "panels:composer-local-sends:";
/** Fields that name this page's own memory. A reload cannot use them. */
const LOCAL_ONLY = new Set(["file", "previewUrl", "localVideoUrl"]);
const restoredDispatches = new WeakSet<PendingComposerSend>();
const queues = new Set<LocalComposerSends>();
const markedForReplacement = new Set<PendingComposerSend>();
const REATTACH_NOTE = "Some attachments could not be saved. Select them again before sending.";

export function pendingSendContent(entry: PendingComposerSend): SentMessagePiece[] {
  const pieces: SentMessagePiece[] = entry.attempt.content.filter(piece => piece.piece !== "video_upload" && piece.piece !== "attachment_upload");
  for (const video of entry.attempt.draftBeforeSend.pendingVideos ?? []) {
    if (video.phase !== "ready" || !video.uploadId) throw new Error("The file is not ready.");
    pieces.push(uploadPiece(video));
  }
  return pieces;
}

/** FIFO includes later text-only sends. A failed upload cannot silently reorder them. */
export class LocalComposerSends {
  entries: PendingComposerSend[] = [];
  private dispatching = false;
  private disposed = false;
  constructor(private dispatch: (entry: PendingComposerSend, content: SentMessagePiece[]) => Promise<boolean>, private changed: () => void) { queues.add(this); }
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
        delete first.resumeAfterUpdate;
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
  /** Only entries from a former renderer need this late receipt check. Live dispatches keep their own promise. */
  async reconcileRestored(reconcile: (messageId: string) => Promise<boolean>): Promise<void> {
    for (const entry of [...this.entries]) {
      if (!restoredDispatches.has(entry)) continue;
      try {
        const accepted = await reconcile(entry.context.messageId);
        if (this.disposed) return;
        if (accepted && this.entries.includes(entry)) {
          this.entries = this.entries.filter(candidate => candidate !== entry);
          restoredDispatches.delete(entry);
          this.changed();
        }
      } catch { /* An unavailable receipt never permits another send. */ }
    }
  }
  prepareUpdateHandoff(): void {
    for (const entry of this.entries) {
      if (entry.phase !== "waiting" || entry.resumeAfterUpdate) continue;
      entry.resumeAfterUpdate = true;
      markedForReplacement.add(entry);
    }
    this.changed();
  }
  cancelUpdateHandoff(): void {
    for (const entry of this.entries) if (markedForReplacement.delete(entry)) delete entry.resumeAfterUpdate;
    this.changed();
  }
  dispose(): void { this.disposed = true; queues.delete(this); }
}

/** Only actual update replacement gives never-dispatched queue entries permission to resume. */
export function prepareLocalSendsUpdateHandoff(): void { for (const queue of queues) queue.prepareUpdateHandoff(); }
export function cancelLocalSendsUpdateHandoff(): void { for (const queue of queues) queue.cancelUpdateHandoff(); }
export function resumeLocalSendsAfterUpdate(entries: readonly PendingComposerSend[], resumed = wasRefusedAttachmentResumed): void {
  for (const entry of entries) {
    if (!entry.resumeAfterUpdate || entry.phase !== "interrupted" || entry.needsReconciliation || entry.needsReattachment) continue;
    if (!(entry.attempt.draftBeforeSend.pendingVideos ?? []).every(video => video.phase === "ready" || resumed(video))) continue;
    entry.phase = "waiting";
    entry.error = undefined;
  }
}

/** This serializer keeps token metadata and text. Update handoff stores File bytes separately in IndexedDB. */
export function rememberLocalSends(key: string, entries: readonly PendingComposerSend[]): void {
  try {
    if (!entries.length) window.sessionStorage.removeItem(PREFIX + key);
    else window.sessionStorage.setItem(PREFIX + key, JSON.stringify(entries, (key, value) => LOCAL_ONLY.has(key) ? undefined : value));
  } catch {
    // A large document/image payload may exceed session storage. Keep the words and
    // video references, with an explicit attachment recovery state, rather than lose text.
    try {
      const compact = entries.map(entry => ({...entry, phase: entry.phase === "dispatching" ? "dispatching" : "interrupted",
        needsReattachment: entry.needsReattachment || entry.attempt.content.some(piece => piece.piece === "image" || piece.piece === "file"),
        error: REATTACH_NOTE,
        attempt: {...entry.attempt, content: entry.attempt.content.filter(piece => piece.piece === "text" || piece.piece === "video_upload" || piece.piece === "attachment_upload"),
          draftBeforeSend: {...entry.attempt.draftBeforeSend, pendingImages: [], pendingFiles: [],
            attachmentOrder: entry.attempt.draftBeforeSend.attachmentOrder?.filter(item => item.kind === "video")}}}));
      window.sessionStorage.setItem(PREFIX + key, JSON.stringify(compact, (name, value) => LOCAL_ONLY.has(name) ? undefined : value));
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
      .map(entry => {
        const needsReattachment = entry.needsReattachment || entry.error === REATTACH_NOTE;
        const recalled: PendingComposerSend = {...entry, attempt: {...entry.attempt, draftBeforeSend: {...entry.attempt.draftBeforeSend,
          pendingVideos: (entry.attempt.draftBeforeSend.pendingVideos ?? []).map(video => hydrateAttachmentFile({...video,
            phase: video.phase === "ready" ? "ready" : "interrupted", file: undefined,
            error: video.phase === "ready" ? undefined : "Select the file again, then press Send."}))}},
          phase: entry.phase === "dispatching" ? "dispatching" : "interrupted",
          needsReconciliation: entry.phase === "dispatching" || entry.needsReconciliation,
          needsReattachment,
          error: needsReattachment ? REATTACH_NOTE : "This message was kept in the browser. Try again or edit it before sending."};
        if (entry.phase === "dispatching") restoredDispatches.add(recalled);
        return recalled;
      });
  } catch { return []; }
}
export function rememberDraftVideo(key: string, videos: readonly import("./contracts").PendingVideo[]): void {
  try {
    const value = videos.map(({file: _file, previewUrl: _previewUrl, localVideoUrl: _localVideoUrl, ...video}) => video);
    if (!value.length) window.sessionStorage.removeItem(PREFIX + "video:" + key);
    else window.sessionStorage.setItem(PREFIX + "video:" + key, JSON.stringify(value));
  } catch { /* best effort */ }
}
export function recallDraftVideo(key: string): import("./contracts").PendingVideo[] {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(PREFIX + "video:" + key) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter(video => video && typeof video.fileName === "string" && typeof video.destination?.id === "string")
      .map(video => hydrateAttachmentFile({...video, phase: video.phase === "ready" ? "validating" : "interrupted", error: "Select the file again, then press Send."}));
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
