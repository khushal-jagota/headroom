import { describe, expect, it } from "vitest";

import {
  beginComposerSend,
  restoreRefusedComposerSend,
  type ComposerDraft
} from "../src/components/conversation/composer/draftTransaction";
import type { RunValues } from "../src/lib/conversation/composer";
import type { PendingConversationImage } from "../src/lib/conversation/pendingImages";
import type { PendingConversationFile } from "../src/lib/conversation/pendingFiles";
import type { PromptDeliveryMode } from "../src/lib/conversation/wire";

function pendingImage(
  id: number,
  fileName: string,
  data: string,
  byteCount: number
): PendingConversationImage {
  return {
    id,
    fileName,
    mediaType: "image/png",
    byteCount,
    data,
    previewUrl: `blob:${fileName}`,
    previewUrlNeedsRevoking: true
  };
}

function pendingFile(id: number, fileName: string, data: string): PendingConversationFile {
  return {
    id,
    fileName,
    mediaType: "application/json",
    byteCount: 2,
    data
  };
}

function draft(overrides: Partial<ComposerDraft> = {}): ComposerDraft {
  return {
    text: "  look here  ",
    pendingImages: [
      pendingImage(7, "first.png", "AQID", 3),
      pendingImage(8, "second.png", "BAU=", 2)
    ],
    pendingFiles: [],
    pickedModel: "sonnet",
    pickedReasoningEffort: "low",
    compositionRevision: 14,
    ...overrides
  };
}

const carriedRunValues: RunValues = {
  model: "sonnet",
  reasoningEffort: "low",
  backendKey: "claude"
};

describe("composer draft transaction", () => {
  it("snapshots the exact draft and carried values into the sent content run", () => {
    const pendingImages = [
      pendingImage(7, "first.png", "AQID", 3),
      pendingImage(8, "second.png", "BAU=", 2)
    ];
    const suppliedDraft = draft({ pendingImages });
    const suppliedRunValues = { ...carriedRunValues };

    const attempt = beginComposerSend(suppliedDraft, suppliedRunValues, "queue");

    expect(attempt.content).toEqual([
      { piece: "text", text: "look here" },
      {
        piece: "image",
        data: "AQID",
        media_type: "image/png",
        file_name: "first.png"
      },
      {
        piece: "image",
        data: "BAU=",
        media_type: "image/png",
        file_name: "second.png"
      }
    ]);
    expect(attempt.carriedRunValues).toEqual({
      model: "sonnet",
      reasoningEffort: "low",
      backendKey: "claude"
    });
    expect(attempt.draftBeforeSend).toEqual(suppliedDraft);
    expect(attempt.draftBeforeSend).not.toBe(suppliedDraft);
    expect(attempt.draftBeforeSend.pendingImages).not.toBe(pendingImages);
    expect(attempt.carriedRunValues).not.toBe(suppliedRunValues);

    pendingImages.push(pendingImage(9, "late.png", "Bg==", 1));
    suppliedRunValues.model = "opus";

    expect(attempt.draftBeforeSend.pendingImages).toHaveLength(2);
    expect(attempt.carriedRunValues.model).toBe("sonnet");
  });

  it("sends and restores files with the rest of a refused draft", () => {
    const attempt = beginComposerSend(
      draft({ pendingFiles: [pendingFile(4, "facts.json", "e30=")] }),
      carriedRunValues,
      "queue"
    );

    expect(attempt.content.at(-1)).toEqual({
      piece: "file",
      data: "e30=",
      media_type: "application/json",
      file_name: "facts.json"
    });
    const restoration = restoreRefusedComposerSend(
      attempt.draftAfterSend,
      attempt,
      20,
      30,
      0
    );
    expect(restoration.restored).toBe(true);
    expect(restoration.nextFileId).toBe(31);
    expect(restoration.draft.pendingFiles).toEqual([
      pendingFile(30, "facts.json", "e30=")
    ]);
  });

  it("restores the sent text, images, picks, ids, and revision after a definite refusal", () => {
    const attempt = beginComposerSend(draft(), carriedRunValues, "queue");

    const restoration = restoreRefusedComposerSend(
      attempt.draftAfterSend,
      attempt,
      20,
      30,
      0
    );

    expect(restoration.restored).toBe(true);
    expect(restoration.nextImageId).toBe(22);
    expect(restoration.nextFileId).toBe(30);
    expect(restoration.draft).toMatchObject({
      text: "look here",
      pickedModel: "sonnet",
      pickedReasoningEffort: "low",
      compositionRevision: 14
    });
    expect(restoration.draft.pendingImages).toEqual([
      {
        id: 20,
        fileName: "first.png",
        mediaType: "image/png",
        byteCount: 3,
        data: "AQID",
        previewUrl: "data:image/png;base64,AQID",
        previewUrlNeedsRevoking: false
      },
      {
        id: 21,
        fileName: "second.png",
        mediaType: "image/png",
        byteCount: 2,
        data: "BAU=",
        previewUrl: "data:image/png;base64,BAU=",
        previewUrlNeedsRevoking: false
      }
    ]);
  });

});
