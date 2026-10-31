import type { PendingVideo } from "../../../lib/conversation/videoUploads/contracts";
import type { RunValues } from "../../../lib/conversation/composer";
import {
  pendingImagesAsPieces,
  restoredPendingImages,
  type PendingConversationImage
} from "../../../lib/conversation/pendingImages";
import {
  pendingFilesAsPieces,
  restoredPendingFiles,
  type PendingConversationFile
} from "../../../lib/conversation/pendingFiles";
import type { SentMessagePiece } from "../../../lib/conversation/wire";
import type { ComposerAttachmentReference } from "./attachments";

export type ComposerDraft = Readonly<{
  text: string;
  attachmentOrder?: readonly ComposerAttachmentReference[];
  pendingImages: readonly PendingConversationImage[];
  pendingFiles: readonly PendingConversationFile[];
  pendingVideos?: readonly PendingVideo[];
  pickedModel: string | null;
  pickedReasoningEffort: string | null;
  pickedBackend?: RunValues["backendKey"];
  effortIsSet?: boolean;
  compositionRevision: number;
}>;

export type ComposerSendAttempt = Readonly<{
  content: readonly SentMessagePiece[];
  carriedRunValues: RunValues;
  draftBeforeSend: ComposerDraft;
  draftAfterSend: ComposerDraft;
}>;

export type RefusedComposerSendRestoration = Readonly<{
  restored: boolean;
  draft: ComposerDraft;
  nextImageId: number;
  nextFileId: number;
}>;

function snapshotDraft(draft: ComposerDraft): ComposerDraft {
  return {
    text: draft.text,
    ...(draft.attachmentOrder === undefined ? {} : { attachmentOrder: [...draft.attachmentOrder] }),
    pendingImages: [...draft.pendingImages],
    pendingFiles: [...draft.pendingFiles],
    pendingVideos: [...(draft.pendingVideos ?? [])],
    pickedModel: draft.pickedModel,
    pickedReasoningEffort: draft.pickedReasoningEffort,
    ...(draft.pickedBackend === undefined ? {} : {pickedBackend: draft.pickedBackend}),
    effortIsSet: draft.effortIsSet,
    compositionRevision: draft.compositionRevision
  };
}

function samePendingImages(
  one: readonly PendingConversationImage[],
  other: readonly PendingConversationImage[]
): boolean {
  return (
    one.length === other.length
    && one.every((image, index) => image === other[index])
  );
}

function samePendingFiles(
  one: readonly PendingConversationFile[],
  other: readonly PendingConversationFile[]
): boolean {
  return one.length === other.length && one.every((file, index) => file === other[index]);
}

function sameAttachmentOrder(
  one: ComposerDraft["attachmentOrder"],
  other: ComposerDraft["attachmentOrder"]
): boolean {
  if (one === undefined || other === undefined) return one === other;
  return one.length === other.length && one.every((reference, index) =>
    reference.kind === other[index]?.kind && reference.id === other[index]?.id
  );
}

function draftEquals(one: ComposerDraft, other: ComposerDraft): boolean {
  return (
    sameAttachmentOrder(one.attachmentOrder, other.attachmentOrder)
    && one.compositionRevision === other.compositionRevision
    && one.text === other.text
    && samePendingImages(one.pendingImages, other.pendingImages)
    && samePendingFiles(one.pendingFiles, other.pendingFiles)
    && (one.pendingVideos ?? []).length === (other.pendingVideos ?? []).length
    && (one.pendingVideos ?? []).every((video, index) => video === other.pendingVideos?.[index])
    && one.pickedModel === other.pickedModel
    && one.pickedReasoningEffort === other.pickedReasoningEffort
    && one.pickedBackend === other.pickedBackend
    && one.effortIsSet === other.effortIsSet
  );
}

function sentText(content: readonly SentMessagePiece[]): string {
  return content
    .filter((piece): piece is Extract<SentMessagePiece, { piece: "text" }> =>
      piece.piece === "text"
    )
    .map((piece) => piece.text)
    .join("");
}

export function beginComposerSend(
  draft: ComposerDraft,
  carriedRunValues: RunValues
): ComposerSendAttempt {
  const draftBeforeSend = snapshotDraft(draft);
  const trimmedText = draftBeforeSend.text.trim();
  const content: SentMessagePiece[] = [
    ...(trimmedText === "" ? [] : [{ piece: "text" as const, text: trimmedText }]),
    ...pendingImagesAsPieces(draftBeforeSend.pendingImages),
    ...pendingFilesAsPieces(draftBeforeSend.pendingFiles),
    ...(draftBeforeSend.pendingVideos ?? []).flatMap(video => video.phase === "ready" && video.uploadId
      ? [{piece: "video_upload" as const, upload_id: video.uploadId}] : [])
  ];
  const draftAfterSend: ComposerDraft = {
    text: "",
    ...(draftBeforeSend.attachmentOrder === undefined ? {} : { attachmentOrder: [] }),
    pendingImages: [],
    pendingFiles: [],
    pendingVideos: [],
    pickedModel: null,
    ...(draftBeforeSend.pickedBackend === undefined ? {} : {pickedBackend: null}),
    pickedReasoningEffort: null,
    compositionRevision: draftBeforeSend.compositionRevision
  };
  return {
    content,
    carriedRunValues: { ...carriedRunValues },
    draftBeforeSend,
    draftAfterSend
  };
}

export function restoreRefusedComposerSend(
  currentDraft: ComposerDraft,
  attempt: ComposerSendAttempt,
  nextImageId: number,
  nextFileId: number,
  attachmentIntakesInFlight: number
): RefusedComposerSendRestoration {
  if (
    attachmentIntakesInFlight !== 0
    || !draftEquals(currentDraft, attempt.draftAfterSend)
  ) {
    return { restored: false, draft: currentDraft, nextImageId, nextFileId };
  }
  const restoredImages = restoredPendingImages(attempt.content, nextImageId);
  const restoredFiles = restoredPendingFiles(attempt.content, nextFileId);
  return {
    restored: true,
    draft: {
      text: sentText(attempt.content),
      ...(attempt.draftBeforeSend.attachmentOrder === undefined ? {} : {
        attachmentOrder: attempt.draftBeforeSend.attachmentOrder.map((reference) => {
          if (reference.kind === "video") return reference;
          const before = reference.kind === "image"
            ? attempt.draftBeforeSend.pendingImages : attempt.draftBeforeSend.pendingFiles;
          const restored = reference.kind === "image" ? restoredImages.images : restoredFiles.files;
          const index = before.findIndex((attachment) => attachment.id === reference.id);
          return { kind: reference.kind, id: restored[index]!.id };
        })
      }),
      pendingImages: restoredImages.images,
      pendingFiles: restoredFiles.files,
      pendingVideos: attempt.draftBeforeSend.pendingVideos ?? [],
      pickedModel: attempt.draftBeforeSend.pickedModel,
      pickedReasoningEffort: attempt.draftBeforeSend.pickedReasoningEffort,
      ...(attempt.draftBeforeSend.pickedBackend === undefined ? {} : {pickedBackend: attempt.draftBeforeSend.pickedBackend}),
      effortIsSet: attempt.draftBeforeSend.effortIsSet,
      compositionRevision: attempt.draftAfterSend.compositionRevision
    },
    nextImageId: restoredImages.nextId,
    nextFileId: restoredFiles.nextId
  };
}
