import { mintOutgoingMessage, reserveOutgoingMessageImages, resetOutgoingImageReservationsForTest } from "../src/lib/conversation/outgoing";
import { describe, expect, it, vi } from "vitest";
import { beginComposerSend, restoreRefusedComposerSend, type ComposerDraft } from "../src/components/conversation/composer/draftTransaction";
import { LocalComposerSends, resumeLocalSendsAfterUpdate, releaseRemovedLocalSends, pendingSendContent, recallLocalSends, rememberLocalSends, type PendingComposerSend } from "../src/lib/conversation/videoUploads/localSends";
import { AttachmentTransfers, VideoTransfers, pendingUploadsAsPieces, restoredPendingUploads, openAttachmentDraft, attachmentTransfersFor, liveAttachmentDrafts, clearAttachmentDraft } from "../src/lib/conversation/videoUploads/uploads";
import type { PendingVideo, UploadDestination, VideoUploadAdapter, VideoUploadStatus } from "../src/lib/conversation/videoUploads/contracts";

const destination: UploadDestination = {kind: "ticket", id: "t_video", expected_conversation_id: null};
// A real File: a video gets a local address for its first frame, which needs a Blob.
const file = Object.assign(new File(["x".repeat(100)], "recording.mp4", {type: "video/mp4"}), {privateBytes: "never persist these"});
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
    expect(pendingSendContent(first).at(-1)).toEqual({piece: "attachment_upload", upload_id: "ready-token"});
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
    attachment.phase = "failed"; attachment.file = file;
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

  it("reload retains its transcript time, text, and ready metadata without submitting the message", async () => {
    const store = new Map<string, string>();
    vi.stubGlobal("window", {sessionStorage: {getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key)}});
    const attachment = video(); attachment.phase = "ready"; attachment.uploadId = "saved-token";
    rememberLocalSends("owner", [{...entry(attachment), sentAtUnixMilliseconds: 1772000000000}]);
    expect([...store.values()].join("")).not.toContain("privateBytes");
    expect([...store.values()].join("")).not.toContain("never persist these");
    const recalled = recallLocalSends("owner");
    expect(recalled[0]?.sentAtUnixMilliseconds).toBe(1772000000000);
    expect(recalled[0]?.attempt.draftBeforeSend.text).toBe("captured words");
    expect(recalled[0]?.attempt.draftBeforeSend.pendingVideos?.[0]?.uploadId).toBe("saved-token");
    expect(recalled[0]?.phase).toBe("interrupted");
    const dispatch = vi.fn(async () => true);
    const queue = new LocalComposerSends(dispatch, () => undefined); queue.entries = recalled;
    await queue.pump(); expect(dispatch).not.toHaveBeenCalled();
  });
});

// Generic tokens must survive the same ready-reference reload boundary as video did.
it("accepts arbitrary and empty files under one cap, and restores ready refs without file bytes", async () => {
  const transfers = new AttachmentTransfers({ reserve: vi.fn(), transfer: vi.fn(), status: vi.fn(async () => ({ ...status("binary-token"), filename: "archive.bin", byte_count: 24 * 1024 * 1024, received_bytes: 24 * 1024 * 1024, media_type: "application/octet-stream" })), cancel: vi.fn() }, () => {});
  const empty = transfers.create({ name: "empty.unknown", size: 0, type: "" } as File, 1, destination);
  expect(empty.byteCount).toBe(0);
  const selected = transfers.create({ name: "archive.bin", size: 24 * 1024 * 1024, type: "application/octet-stream" } as File, 2, destination);
  selected.phase = "ready"; selected.uploadId = "binary-token";
  expect(() => transfers.create({ name: "too-large.bin", size: 250 * 1024 * 1024 + 1 } as File, 3, destination)).toThrow("250 MiB");
  const pieces = pendingUploadsAsPieces([selected]);
  expect(pieces).toEqual([{ piece: "attachment_upload", upload_id: "binary-token" }]);
  const [restored] = restoredPendingUploads(pieces, destination);
  expect(restored.phase).toBe("validating"); expect(restored.file).toBeUndefined();
  await transfers.validate(restored);
  expect(restored.fileName).toBe("archive.bin"); expect(restored.byteCount).toBe(24 * 1024 * 1024);
  expect(pendingUploadsAsPieces([restored])).toEqual(pieces);
});

// An artifact mounts a second composer over the same draft while a transfer can still run.
// Reuse the current adapter fixture to prove it keeps one owner and releases accepted refs.
it("keeps a pending streamed file across composer remounts and clears its ready reference", async () => {
  const store = new Map<string, string>();
  vi.stubGlobal("window", {sessionStorage: {getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key)}});
  const transfer = deferred<VideoUploadStatus>();
  const adapter = { reserve: vi.fn(async () => status("shared-file")), transfer: vi.fn(async () => transfer.promise), status: vi.fn(async () => status("shared-file")), cancel: vi.fn(async () => {}) };
  const firstOwner = attachmentTransfersFor("artifact-draft", adapter);
  const pending = firstOwner.create(file, 1, destination);
  const ready = { ...pending, id: 0, uploadId: "older-ready", phase: "ready" as const, file: undefined };
  openAttachmentDraft("artifact-draft", [ready, pending]);
  const completing = firstOwner.start(pending);
  const secondOwner = attachmentTransfersFor("artifact-draft", adapter);
  expect(secondOwner).toBe(firstOwner);
  const reopened = openAttachmentDraft("artifact-draft", [{ ...ready }]);
  expect(reopened).toEqual([ready, pending]);
  expect(reopened[1]).toBe(pending);
  await Promise.resolve();
  transfer.resolve(status("shared-file"));
  await completing;
  expect(adapter.transfer).toHaveBeenCalledOnce();
  expect(pending.file).toBeUndefined();
  expect(pendingUploadsAsPieces(liveAttachmentDrafts.get("artifact-draft")!)).toEqual([{piece:"attachment_upload",upload_id:"older-ready"},{piece:"attachment_upload",upload_id:"shared-file"}]);
  clearAttachmentDraft("artifact-draft");
  expect(liveAttachmentDrafts.get("artifact-draft")).toEqual([]);
  expect(openAttachmentDraft("artifact-draft", [{ ...ready }])).toEqual([]);
  expect([...store.values()].join("")).not.toContain("shared-file");
});

// Upload progress reaches 100% before the PUT returns its terminal status. The component
// observes validating then, but only the live transfer can decide its ready/failed outcome.
it("does not race a live validating PUT with a status GET, and deduplicates resumed validation", async () => {
  const uploading = deferred<VideoUploadStatus>();
  const resumed = deferred<VideoUploadStatus>();
  const adapter = { reserve: vi.fn(async () => status("live-file")),
    transfer: vi.fn(async (_id: string, selected: File, _signal: AbortSignal, progress: (bytes: number) => void) => {progress(selected.size); return uploading.promise;}),
    status: vi.fn(async () => resumed.promise), cancel: vi.fn(async () => {}) };
  const transfers = new AttachmentTransfers(adapter, () => {});
  const pending = transfers.create(file, 1, destination);
  const completing = transfers.start(pending);
  await Promise.resolve();
  expect(pending.phase).toBe("validating");
  await transfers.validate(pending);
  expect(adapter.status).not.toHaveBeenCalled();
  uploading.resolve(status("live-file"));
  await completing;
  expect(pending.phase).toBe("ready");
  const restored = {...pending, phase:"validating" as const};
  const first = transfers.validate(restored);
  const second = transfers.validate(restored);
  expect(adapter.status).toHaveBeenCalledOnce();
  resumed.resolve(status("live-file"));
  await Promise.all([first, second]);
  expect(restored.phase).toBe("ready");
});

// Existing reload cases serialize token metadata only. This case proves that the actual
// local File and an explicitly refused reservation return to the upload owner together.
it("restores File bytes after screen replacement and resumes only proven refused reservations", async () => {
  const { prepareAttachmentUpdateHandoff, restoreAttachmentUpdateHandoff } = await import("../src/lib/conversation/videoUploads/updateHandoff");
  const { recallDraftVideo, rememberDraftVideo } = await import("../src/lib/conversation/videoUploads/localSends");
  const rows = new Map<string, unknown>();
  const memory = new Map<string, string>();
  vi.stubGlobal("sessionStorage", { getItem: (key: string) => memory.get(key) ?? null, setItem: (key: string, value: string) => memory.set(key, value), removeItem: (key: string) => memory.delete(key) });
  vi.stubGlobal("window", {sessionStorage});
  vi.stubGlobal("indexedDB", {open: () => {
    const request: any = {};
    queueMicrotask(() => {
      request.result = {close() {}, transaction() {
        const transaction: any = {objectStore: () => ({
          put(value: any) {rows.set(value.key, structuredClone(value)); return {result: value.key};},
          getAll() {return {result: [...rows.values()]};},
          delete(key: string) {rows.delete(key); return {result: undefined};}
        })};
        queueMicrotask(() => transaction.oncomplete());
        return transaction;
      }};
      request.onsuccess();
    });
    return request;
  }});
  const adapter: VideoUploadAdapter = {
    reserve: vi.fn(async () => status("restored-reservation")),
    transfer: vi.fn(async (_id, bytes) => {expect(await bytes.text()).toBe("preserved-file-bytes"); return status("restored-reservation");}),
    status: vi.fn(async () => status()), cancel: vi.fn(async () => undefined)
  };
  const original = new AttachmentTransfers(adapter, () => undefined);
  const attachment = original.create(new File(["preserved-file-bytes"], "update.txt"), 8123, destination);
  const reserved = original.create(new File(["preserved-file-bytes"], "reserved.txt"), 8124, destination);
  reserved.uploadId = "reserved-before-update";
  rememberDraftVideo("update-test", [attachment, reserved]);
  await prepareAttachmentUpdateHandoff();
  original.dispose();
  await restoreAttachmentUpdateHandoff([]);
  const attachments = recallDraftVideo("update-test");
  expect(await attachments[0]!.file!.text()).toBe("preserved-file-bytes");
  const owner = new AttachmentTransfers(adapter, () => undefined);
  owner.restore(attachments);
  expect(adapter.reserve).not.toHaveBeenCalled();
  await restoreAttachmentUpdateHandoff([{id:"refused", input:"/api/conversation/video-uploads", method:"POST", body:JSON.stringify({destination, filename:"update.txt", byte_count:20})}]);
  await vi.waitFor(() => expect(attachments[0]!.phase).toBe("ready"));
  expect(adapter.reserve).toHaveBeenCalledOnce();
  expect(adapter.transfer).toHaveBeenCalledOnce();
  expect(attachments[1]!.phase).toBe("interrupted");
  await restoreAttachmentUpdateHandoff([{id:"refused-put", input:"/api/conversation/video-uploads/reserved-before-update/bytes", method:"PUT", body:null}]);
  await vi.waitFor(() => expect(attachments[1]!.phase).toBe("ready"));
  expect(adapter.reserve).toHaveBeenCalledOnce();
  expect(adapter.transfer).toHaveBeenLastCalledWith("reserved-before-update", expect.any(File), expect.any(AbortSignal), expect.any(Function));
  owner.dispose();
});

// Existing reload coverage deliberately interrupts sends. An update checkpoint must resume
// only never-dispatched intent, and upload refusal proof must not bless an uncertain file.
it("resumes update-checkpointed unsent messages in FIFO after proven upload recovery", async () => {
  const store = new Map<string, string>();
  vi.stubGlobal("window", {sessionStorage: {getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key)}});
  const safe = entry(video(), "safe");
  const later = entry(undefined, "later");
  const uncertain = entry({...video(), id: 2}, "uncertain");
  const dispatching = {...entry(undefined, "already-dispatched"), phase: "dispatching" as const};
  const failed = {...entry(undefined, "failed"), phase: "failed" as const};
  const old = new LocalComposerSends(async () => true, () => rememberLocalSends("update", old.entries));
  old.entries = [safe, later, uncertain, dispatching, failed];
  old.prepareUpdateHandoff();
  expect(safe.resumeAfterUpdate).toBe(true);
  expect(dispatching.resumeAfterUpdate).toBeUndefined();
  expect(failed.resumeAfterUpdate).toBeUndefined();
  old.cancelUpdateHandoff();
  expect(safe.resumeAfterUpdate).toBeUndefined();
  old.prepareUpdateHandoff();
  old.dispose();
  const recalled = recallLocalSends("update");
  resumeLocalSendsAfterUpdate(recalled, upload => upload.id === 1);
  expect(recalled.map(item => item.phase)).toEqual(["waiting", "waiting", "interrupted", "dispatching", "interrupted"]);
  const attachment = recalled[0]!.attempt.draftBeforeSend.pendingVideos![0]!;
  attachment.uploadId = "ready-token";
  attachment.phase = "ready";
  const dispatched: string[] = [];
  const queue = new LocalComposerSends(async item => { dispatched.push(item.context.messageId); return true; }, () => {});
  queue.entries = recalled;
  await queue.pump();
  expect(dispatched).toEqual(["safe", "later"]);
  expect(queue.entries[0]?.context.messageId).toBe("uncertain");
  expect(safe.resumeAfterUpdate).toBe(true);
  expect(recalled[0]?.resumeAfterUpdate).toBeUndefined();
  queue.dispose();
});

// A journal replay can reach the server after the composer's first receipt read.
it("clears only a restored dispatch after its late replay receipt and advances later unsent intent", async () => {
  const store = new Map<string, string>();
  vi.stubGlobal("window", {sessionStorage: {getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key)}});
  const earlier = {...entry(undefined, "restored"), phase: "dispatching" as const};
  const later = {...entry(undefined, "later"), resumeAfterUpdate: true};
  rememberLocalSends("late-receipt", [earlier, later]);
  const recalled = recallLocalSends("late-receipt");
  const dispatch = vi.fn(async (_entry: PendingComposerSend) => true);
  let queue!: LocalComposerSends;
  queue = new LocalComposerSends(dispatch, () => { resumeLocalSendsAfterUpdate(queue.entries); void queue.pump(); });
  queue.entries = recalled;
  const receipt = vi.fn(async () => false);
  await queue.reconcileRestored(receipt);
  recalled[0]!.phase = "interrupted";
  recalled[0]!.needsReconciliation = false;
  resumeLocalSendsAfterUpdate(recalled);
  await queue.pump();
  expect(dispatch).not.toHaveBeenCalled();
  receipt.mockResolvedValue(true);
  await queue.reconcileRestored(receipt);
  await queue.pump();
  expect(dispatch).toHaveBeenCalledOnce();
  expect(dispatch.mock.calls[0]?.[0].context.messageId).toBe("later");
  expect(queue.entries).toEqual([]);
  queue.dispose();
});
