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

export type ComposerDraft = Readonly<{
  text: string;
  pendingImages: readonly PendingConversationImage[];
  pendingFiles: readonly PendingConversationFile[];
  pickedModel: string | null;
  pickedReasoningEffort: string | null;
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
    pendingImages: [...draft.pendingImages],
    pendingFiles: [...draft.pendingFiles],
    pickedModel: draft.pickedModel,
    pickedReasoningEffort: draft.pickedReasoningEffort,
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

function draftEquals(one: ComposerDraft, other: ComposerDraft): boolean {
  return (
    one.compositionRevision === other.compositionRevision
    && one.text === other.text
    && samePendingImages(one.pendingImages, other.pendingImages)
    && samePendingFiles(one.pendingFiles, other.pendingFiles)
    && one.pickedModel === other.pickedModel
    && one.pickedReasoningEffort === other.pickedReasoningEffort
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
    ...pendingFilesAsPieces(draftBeforeSend.pendingFiles)
  ];
  const draftAfterSend: ComposerDraft = {
    text: "",
    pendingImages: [],
    pendingFiles: [],
    pickedModel: null,
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
      pendingImages: restoredImages.images,
      pendingFiles: restoredFiles.files,
      pickedModel: attempt.draftBeforeSend.pickedModel,
      pickedReasoningEffort: attempt.draftBeforeSend.pickedReasoningEffort,
      compositionRevision: attempt.draftAfterSend.compositionRevision
    },
    nextImageId: restoredImages.nextId,
    nextFileId: restoredFiles.nextId
  };
}
