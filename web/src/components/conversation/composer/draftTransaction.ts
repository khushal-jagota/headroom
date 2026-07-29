import type { RunValues } from "../../../lib/conversation/composer";
import {
  pendingImagesAsPieces,
  restoredPendingImages,
  type PendingConversationImage
} from "../../../lib/conversation/pendingImages";
import type {
  PromptDeliveryMode,
  SentMessagePiece
} from "../../../lib/conversation/wire";

export type ComposerDraft = Readonly<{
  text: string;
  pendingImages: readonly PendingConversationImage[];
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
}>;

function snapshotDraft(draft: ComposerDraft): ComposerDraft {
  return {
    text: draft.text,
    pendingImages: [...draft.pendingImages],
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

function draftEquals(one: ComposerDraft, other: ComposerDraft): boolean {
  return (
    one.compositionRevision === other.compositionRevision
    && one.text === other.text
    && samePendingImages(one.pendingImages, other.pendingImages)
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
  carriedRunValues: RunValues,
  mode: PromptDeliveryMode
): ComposerSendAttempt {
  const draftBeforeSend = snapshotDraft(draft);
  const trimmedText = draftBeforeSend.text.trim();
  const content: SentMessagePiece[] = [
    ...(trimmedText === "" ? [] : [{ piece: "text" as const, text: trimmedText }]),
    ...pendingImagesAsPieces(draftBeforeSend.pendingImages)
  ];
  const retainsRunValuePicks = mode === "steer";
  const draftAfterSend: ComposerDraft = {
    text: "",
    pendingImages: [],
    pickedModel: retainsRunValuePicks ? draftBeforeSend.pickedModel : null,
    pickedReasoningEffort:
      retainsRunValuePicks ? draftBeforeSend.pickedReasoningEffort : null,
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
  imageIntakesInFlight: number
): RefusedComposerSendRestoration {
  if (
    imageIntakesInFlight !== 0
    || !draftEquals(currentDraft, attempt.draftAfterSend)
  ) {
    return { restored: false, draft: currentDraft, nextImageId };
  }
  const restoredImages = restoredPendingImages(attempt.content, nextImageId);
  return {
    restored: true,
    draft: {
      text: sentText(attempt.content),
      pendingImages: restoredImages.images,
      pickedModel: attempt.draftBeforeSend.pickedModel,
      pickedReasoningEffort: attempt.draftBeforeSend.pickedReasoningEffort,
      compositionRevision: attempt.draftAfterSend.compositionRevision
    },
    nextImageId: restoredImages.nextId
  };
}
