import { describe, expect, it } from "vitest";
import {
  ASK_SCENES,
  answerSentence,
  askKeyFromEvent,
  commandForAskKey,
  emptyDraft,
  sceneById
} from "../src/lib/asks/preview";

const single = sceneById("single").asks[0];
const multi = sceneById("multi").asks[0];
const approval = sceneById("approval").asks[0];
const text = sceneById("text").asks[0];

describe("ask preview keyboard", () => {
  it("commits a single choice from its number and only highlights an approval", () => {
    expect(commandForAskKey(single, emptyDraft(), -1, { name: "digit", digit: 2 })).toEqual({
      type: "commit-choice",
      choiceId: "stop"
    });
    expect(commandForAskKey(approval, emptyDraft(), -1, { name: "digit", digit: 3 })).toEqual({
      type: "cursor",
      cursor: 2
    });
    expect(commandForAskKey(approval, emptyDraft(), 2, { name: "commit" })).toEqual({
      type: "commit-choice",
      choiceId: "allow_once"
    });
    expect(commandForAskKey(approval, emptyDraft(), -1, { name: "commit" })).toEqual({ type: "none" });
  });

  it("toggles a multi-select from a number without sending it", () => {
    expect(commandForAskKey(multi, emptyDraft(), -1, { name: "digit", digit: 1 })).toEqual({
      type: "toggle",
      choiceId: "outcome",
      cursor: 0
    });
    expect(commandForAskKey(multi, emptyDraft(), 0, { name: "toggle" })).toEqual({
      type: "toggle",
      choiceId: "outcome",
      cursor: 0
    });
  });

  it("walks choices with the arrow keys and wraps", () => {
    expect(commandForAskKey(single, emptyDraft(), -1, { name: "down" })).toEqual({ type: "cursor", cursor: 0 });
    expect(commandForAskKey(single, emptyDraft(), 0, { name: "up" })).toEqual({ type: "cursor", cursor: 2 });
    expect(commandForAskKey(single, emptyDraft(), 2, { name: "down" })).toEqual({ type: "cursor", cursor: 0 });
  });

  it("commits a typed answer on enter and leaves shift-enter to the field", () => {
    const draft = emptyDraft();
    expect(commandForAskKey(text, draft, -1, { name: "commit" })).toEqual({ type: "none" });
    const ready = { ...draft, text: { title: "Quiet asks" } };
    expect(commandForAskKey(text, ready, -1, { name: "commit" })).toEqual({ type: "commit" });
    expect(answerSentence(text, ready)).toBe("Quiet asks");
    expect(askKeyFromEvent(
      { key: "Enter", metaKey: false, ctrlKey: false, altKey: false, shiftKey: true },
      true
    )).toBeNull();
    expect(askKeyFromEvent(
      { key: "1", metaKey: false, ctrlKey: false, altKey: false, shiftKey: false },
      true
    )).toBeNull();
  });

  it("names an approval by the action that was chosen", () => {
    expect(answerSentence(approval, emptyDraft(), "allow_once")).toBe("Approve");
    expect(answerSentence(approval, emptyDraft(), "reject_once")).toBe("Decline");
  });

  it("keeps an example for each live shape", () => {
    const ids = ASK_SCENES.map((scene) => scene.id);
    expect(ids).toEqual(["single", "steps", "multi", "text", "numbered", "approval", "long", "pending"]);
    expect(sceneById("numbered").asks[0].production).toBe("permission");
    expect(sceneById("pending").asks).toHaveLength(3);
  });
});
