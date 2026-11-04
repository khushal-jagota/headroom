import { mintOutgoingMessage, reserveOutgoingMessageImages, resetOutgoingImageReservationsForTest } from "../src/lib/conversation/outgoing";
import { describe, expect, it, vi } from "vitest";
import { beginComposerSend, restoreRefusedComposerSend, type ComposerDraft } from "../src/components/conversation/composer/draftTransaction";
import { LocalComposerSends, releaseRemovedLocalSends, pendingSendContent, recallLocalSends, rememberLocalSends, type PendingComposerSend } from "../src/lib/conversation/videoUploads/localSends";
import { VideoTransfers } from "../src/lib/conversation/videoUploads/uploads";
import type { PendingVideo, UploadDestination, VideoUploadAdapter, VideoUploadStatus } from "../src/lib/conversation/videoUploads/contracts";

const destination: UploadDestination = {kind: "ticket", id: "t_video", expected_conversation_id: null};
const file = {name: "recording.mp4", size: 100, type: "video/mp4", privateBytes: "never persist these"} as unknown as File;
function video(): PendingVideo {
  return {id: 1, fileName: file.name, byteCount: 100, mediaType: file.type, destination: {...destination}, receivedBytes: 0, phase: "uploading", file};
}
function draft(attachment?: PendingVideo, text = "captured words"): ComposerDraft {
  return {text, pendingImages: [], pendingFiles: [], pendingVideos: attachment ? [attachment] : [],
    attachmentOrder: attachment ? [{kind: "video", id: attachment.id}] : [], pickedModel: "first-model", pickedReasoningEffort: "high", compositionRevision: 3};
}
function entry(attachment?: PendingVideo, messageId = "first"): PendingComposerSend {
  return {context: {messageId, expectedConversationId: null, destination: {...destination}}, mode: "queue", phase: "waiting",
    attempt: beginComposerSend(draft(attachment), {model: "first-model", reasoningEffort: "high"})};
}
function status(upload_id = "upload-1"): VideoUploadStatus {
  return {upload_id, destination, filename: file.name, byte_count: file.size, received_bytes: file.size,
    state: "ready", media_type: file.type, expires_at: 123456, error: null};
}
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject}; }

// These protect a new browser-only boundary: a locally requested send has not yet reached
// the server. Existing outgoing tests begin after dispatch and cannot prove this intent.
describe("video attachment send intent", () => {
  it("captures content/options once, preserves order, and dispatches only requested messages after validation", async () => {
    const attachment = video(); const sent: string[] = [];
    const queue = new LocalComposerSends(async (pending, content) => {
      sent.push(pending.context.messageId);
      expect(pending.attempt.carriedRunValues.model).toBe("first-model");
      expect(content[0]).toEqual({piece: "text", text: "captured words"}); return true;
    }, () => undefined);
    const first = entry(attachment); const second = entry(undefined, "second");
    queue.enqueue(first); queue.enqueue(second);
    expect(sent).toEqual([]);
    attachment.uploadId = "ready-token"; attachment.phase = "validating";
    await queue.pump(); expect(sent).toEqual([]);
    // Later edits are another draft and cannot change the queued snapshot.
    const laterDraft = {...draft(), text: "later words", pickedModel: "another-model"};
    expect(laterDraft.text).not.toBe(first.attempt.draftBeforeSend.text);
    attachment.phase = "ready";
    expect(pendingSendContent(first).at(-1)).toEqual({piece: "video_upload", upload_id: "ready-token"});
    await queue.pump(); expect(sent).toEqual(["first", "second"]); expect(queue.entries).toEqual([]);
  });

  it("upload completion before Send never submits; failed upload retries the whole file and resumes explicit Send", async () => {
    const transfer = deferred<VideoUploadStatus>();
    let attempts = 0; let changed = 0; const dispatch = vi.fn(async () => true);
    const queue = new LocalComposerSends(dispatch, () => undefined);
    const adapter: VideoUploadAdapter = {
      reserve: vi.fn(async () => status(`upload-${++attempts}`)),
      transfer: vi.fn(async (_id, _file, _signal, progress) => {progress(100); if (attempts === 1) return transfer.promise; return status("upload-2");}),
      status: vi.fn(async () => status()), cancel: vi.fn(async () => undefined)
    };
    const uploads = new VideoTransfers(adapter, () => {changed++; void queue.pump();});
    const attachment = uploads.create(file, 1, destination);
    const firstUpload = uploads.start(attachment);
    await Promise.resolve(); expect(attachment.phase).toBe("validating");
    transfer.reject(new Error("connection failed")); await firstUpload;
    expect(attachment.phase).toBe("failed"); expect(dispatch).not.toHaveBeenCalled();
    // Retrying a draft still has no send intent.
    await uploads.start(attachment); expect(attachment.phase).toBe("ready");
    expect(dispatch).not.toHaveBeenCalled();
    attachment.phase = "failed";
    queue.enqueue(entry(attachment)); await uploads.start(attachment); await queue.pump();
    expect(adapter.transfer).toHaveBeenCalledTimes(3);
    expect(adapter.cancel).toHaveBeenCalledWith("upload-1");
    expect(dispatch).toHaveBeenCalledOnce(); expect(changed).toBeGreaterThan(0);
  });

  it("cancel removes the requested send and cannot overwrite a newer draft", async () => {
    const attachment = video(); const pending = entry(attachment);
    const dispatch = vi.fn(async () => true);
    const queue = new LocalComposerSends(dispatch, () => undefined);
    queue.enqueue(pending);
    pending.phase = "interrupted";
    const newer = {...pending.attempt.draftAfterSend, text: "new words", compositionRevision: 4};
    const result = restoreRefusedComposerSend(newer, pending.attempt, 1, 1, 0);
    expect(result.restored).toBe(false); expect(result.draft.text).toBe("new words");
    queue.remove(pending.context.messageId);
    attachment.uploadId = "ready-token"; attachment.phase = "ready";
    await queue.pump(); expect(dispatch).not.toHaveBeenCalled();
    const restoration = restoreRefusedComposerSend(pending.attempt.draftAfterSend, pending.attempt, 1, 1, 0);
    expect(restoration.restored).toBe(true); expect(restoration.draft.text).toBe("captured words");
    expect(restoration.draft.pendingVideos?.[0]?.uploadId).toBe("ready-token");
  });

  it("an interrupted earlier message blocks later sends until explicitly removed", async () => {
    const dispatch = vi.fn(async (_entry: PendingComposerSend, _content: unknown) => true);
    const queue = new LocalComposerSends(dispatch, () => undefined);
    const earlier = entry(video()); earlier.phase = "interrupted";
    queue.enqueue(earlier); queue.enqueue(entry(undefined, "later"));
    await queue.pump(); expect(dispatch).not.toHaveBeenCalled();
    queue.remove("first"); await Promise.resolve();
    expect(dispatch).toHaveBeenCalledOnce();
    expect(dispatch.mock.calls[0]?.[0].context.messageId).toBe("later");
  });

  it("an ambiguous dispatch failure retains its original identity and requires reconciliation", async () => {
    const dispatch = vi.fn(async () => {throw new Error("connection lost");});
    const queue = new LocalComposerSends(dispatch, () => undefined);
    const earlier = entry(undefined); queue.enqueue(earlier); await Promise.resolve();
    expect(earlier.phase).toBe("failed"); expect(earlier.needsReconciliation).toBe(true);
    queue.enqueue(entry(undefined, "later")); await queue.pump();
    expect(dispatch).toHaveBeenCalledOnce(); expect(queue.entries[0]?.context.messageId).toBe("first");
  });

  it("receipt reconciliation releases the removed local image reservation", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("window", {sessionStorage: {getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key),
      get length() {return store.size;}, key: (index: number) => [...store.keys()][index] ?? null}});
    resetOutgoingImageReservationsForTest();
    const message = mintOutgoingMessage({content: [{piece: "image", media_type: "image/png", data: "A".repeat(4 * 1024 * 1024)}], senderLabel: "owner", mode: "queue"});
    const local = entry(); local.context.messageId = message.messageId;
    expect(reserveOutgoingMessageImages(message)).toBe(true);
    const next = {...message, messageId: "next-message"};
    expect(reserveOutgoingMessageImages(next)).toBe(false);
    rememberLocalSends("owner", [local]);
    // Same transition used after a persisted receipt settles a restored dispatch.
    rememberLocalSends("owner", []); releaseRemovedLocalSends([local], []);
    expect(reserveOutgoingMessageImages(next)).toBe(true);
    resetOutgoingImageReservationsForTest();
  });

  it("reload retains text and ready metadata without video bytes and requires explicit Send again", async () => {
    const store = new Map<string, string>();
    vi.stubGlobal("window", {sessionStorage: {getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key)}});
    const attachment = video(); attachment.phase = "ready"; attachment.uploadId = "saved-token";
    rememberLocalSends("owner", [entry(attachment)]);
    expect([...store.values()].join("")).not.toContain("privateBytes");
    expect([...store.values()].join("")).not.toContain("never persist these");
    const recalled = recallLocalSends("owner");
    expect(recalled[0]?.attempt.draftBeforeSend.text).toBe("captured words");
    expect(recalled[0]?.attempt.draftBeforeSend.pendingVideos?.[0]?.uploadId).toBe("saved-token");
    expect(recalled[0]?.phase).toBe("interrupted");
    const dispatch = vi.fn(async () => true);
    const queue = new LocalComposerSends(dispatch, () => undefined); queue.entries = recalled;
    await queue.pump(); expect(dispatch).not.toHaveBeenCalled();
  });
});
