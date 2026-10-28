import { describe, expect, it } from "vitest";
import { beginComposerSend, restoreRefusedComposerSend, type ComposerDraft } from "../src/components/conversation/composer/draftTransaction";
import { orderedComposerAttachments } from "../src/components/conversation/composer/attachments";

function draft(): ComposerDraft {
  return {
    text: "keep the draft", pickedModel: "sonnet", pickedReasoningEffort: "high", compositionRevision: 4,
    pendingImages: [1, 2].map(id => ({id, fileName: `image-${id}.png`, mediaType: "image/png", byteCount: 1,
      data: "YQ==", previewUrl: `blob:old-${id}`, previewUrlNeedsRevoking: true})),
    pendingFiles: [{id:1,fileName:"notes.md",mediaType:"text/markdown",byteCount:1,data:"YQ=="}],
    attachmentOrder: [{kind:"image",id:1},{kind:"file",id:1},{kind:"image",id:2}]
  };
}

describe("ordered attachments after a refused send", () => {
  it("restores the same mixed draft with fresh identities and retained run choices", () => {
    const selectedDraft = {...draft(), pickedReasoningEffort: null, selectionKind: "provider_alias" as const, effortIsSet: true};
    const attempt = beginComposerSend(selectedDraft, {model:"sonnet",reasoningEffort:null,selectionKind:"provider_alias",effortIsSet:true});
    expect(attempt.draftAfterSend.attachmentOrder).toEqual([]);
    const result = restoreRefusedComposerSend(attempt.draftAfterSend, attempt, 8, 12, 0);
    expect(result.restored).toBe(true);
    expect(result.draft.attachmentOrder).toEqual([{kind:"image",id:8},{kind:"file",id:12},{kind:"image",id:9}]);
    expect(orderedComposerAttachments(result.draft.attachmentOrder!, result.draft.pendingImages,
      result.draft.pendingFiles).map(attachment => attachment.value.fileName))
      .toEqual(["image-1.png","notes.md","image-2.png"]);
    expect(result.draft.pickedModel).toBe("sonnet");
    expect(result.draft.pickedReasoningEffort).toBeNull();
    expect(result.draft.selectionKind).toBe("provider_alias");
    expect(result.draft.effortIsSet).toBe(true);
    expect(attempt.carriedRunValues).toEqual({model:"sonnet",reasoningEffort:null,selectionKind:"provider_alias",effortIsSet:true});
    const changedKind = {...attempt.draftAfterSend, selectionKind: "concrete" as const};
    expect(restoreRefusedComposerSend(changedKind, attempt, 8, 12, 0).restored).toBe(false);
    expect(result.draft.pendingImages.every(image => !image.previewUrlNeedsRevoking)).toBe(true);
    expect(result.nextImageId).toBe(10);
    expect(result.nextFileId).toBe(13);
  });

  it("leaves a newer draft and pending attachment intake untouched", () => {
    const attempt = beginComposerSend(draft(), {model:null,reasoningEffort:null});
    const newer = {...attempt.draftAfterSend,text:"new words",compositionRevision:5};
    const result = restoreRefusedComposerSend(newer, attempt, 8, 12, 0);
    expect(result.restored).toBe(false);
    expect(result.draft).toBe(newer);
    const intake = restoreRefusedComposerSend(attempt.draftAfterSend, attempt, 8, 12, 1);
    expect(intake.restored).toBe(false);
    expect(intake.draft).toBe(attempt.draftAfterSend);
  });
});
