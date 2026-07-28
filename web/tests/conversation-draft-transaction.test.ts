import { describe, expect, it } from "vitest";

import {
  beginComposerSend,
  restoreRefusedComposerSend,
  type ComposerDraft
} from "../src/components/conversation/composer/draftTransaction";
import type { RunValues } from "../src/lib/conversation/composer";
import type { PendingConversationImage } from "../src/lib/conversation/pendingImages";
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

function draft(overrides: Partial<ComposerDraft> = {}): ComposerDraft {
  return {
    text: "  look here  ",
    pendingImages: [
      pendingImage(7, "first.png", "AQID", 3),
      pendingImage(8, "second.png", "BAU=", 2)
    ],
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

    const attempt = beginComposerSend(suppliedDraft, suppliedRunValues, "run_when_free");

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

  it("omits text that is empty after trimming", () => {
    const attempt = beginComposerSend(
      draft({ text: " \n ", pendingImages: [pendingImage(1, "only.png", "AQ==", 1)] }),
      carriedRunValues,
      "run_when_free"
    );

    expect(attempt.content).toEqual([
      {
        piece: "image",
        data: "AQ==",
        media_type: "image/png",
        file_name: "only.png"
      }
    ]);
  });

  it.each([
    ["steer", "sonnet", "low"],
    ["run_when_free", null, null],
    ["send_now", null, null]
  ] as const)(
    "%s clears sent content and leaves the contracted pending run values",
    (mode, expectedModel, expectedEffort) => {
      const attempt = beginComposerSend(draft(), carriedRunValues, mode);

      expect(attempt.draftAfterSend).toEqual({
        text: "",
        pendingImages: [],
        pickedModel: expectedModel,
        pickedReasoningEffort: expectedEffort,
        compositionRevision: 14
      });
      expect(attempt.draftAfterSend.pendingImages).not.toBe(
        attempt.draftBeforeSend.pendingImages
      );
    }
  );

  it("restores the sent text, images, picks, ids, and revision after a definite refusal", () => {
    const attempt = beginComposerSend(draft(), carriedRunValues, "run_when_free");

    const restoration = restoreRefusedComposerSend(
      attempt.draftAfterSend,
      attempt,
      20,
      0
    );

    expect(restoration.restored).toBe(true);
    expect(restoration.nextImageId).toBe(22);
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

  it.each([
    ["revision changed", { compositionRevision: 15 }, 0],
    ["text changed", { text: "newer draft" }, 0],
    ["images changed", { pendingImages: [pendingImage(30, "new.png", "Bw==", 1)] }, 0],
    ["model changed", { pickedModel: "opus" }, 0],
    ["effort changed", { pickedReasoningEffort: "high" }, 0],
    ["image intake is in flight", {}, 1]
  ] satisfies readonly [
    string,
    Partial<ComposerDraft>,
    number
  ][])("does not restore when %s", (_label, changed, imageIntakesInFlight) => {
    const attempt = beginComposerSend(draft(), carriedRunValues, "run_when_free");
    const currentDraft = { ...attempt.draftAfterSend, ...changed };

    const restoration = restoreRefusedComposerSend(
      currentDraft,
      attempt,
      40,
      imageIntakesInFlight
    );

    expect(restoration).toEqual({
      restored: false,
      draft: currentDraft,
      nextImageId: 40
    });
    expect(restoration.draft).toBe(currentDraft);
  });
});
