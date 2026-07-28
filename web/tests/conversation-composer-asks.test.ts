import { describe, expect, it } from "vitest";

import {
  askActions,
  askChoiceForDigit,
  askIsGeneric,
  askPlaceholder,
  askQuestionChoices,
  askShape
} from "../src/lib/conversation/composer";

describe("Conversation composer asks", () => {
  it("keeps backend permission actions in commitment order", () => {
    const ask = {
      options: [
        {
          option_id: "allow-always",
          label: "Always allow this session",
          option_kind: "allow_always"
        },
        { option_id: "allow-once", label: "Approve once", option_kind: "allow_once" },
        { option_id: "reject", label: "Decline", option_kind: "reject_once" }
      ]
    };

    const actions = askActions(ask);

    expect(actions.map((action) => action.act === "cancel_turn" ? "cancel" : action.optionId))
      .toEqual(["cancel", "reject", "allow-always", "allow-once"]);
    expect(actions.map((action) => action.emphasis))
      .toEqual(["cancel", "decline", "option", "primary"]);
    expect(actions.every((action) => action.supplied)).toBe(true);
    expect(askIsGeneric(ask)).toBe(false);
  });

  it.each([null, {}, { options: [] }])(
    "recovers the three safe actions for an incomplete ask",
    (ask) => {
      const actions = askActions(ask);

      expect(actions.map((action) => action.label))
        .toEqual(["Cancel turn", "Decline", "Approve once"]);
      expect(actions.map((action) => action.act))
        .toEqual(["cancel_turn", "answer", "answer"]);
      expect(actions.slice(1).map((action) => action.supplied)).toEqual([false, false]);
      expect(askIsGeneric(ask)).toBe(true);
    }
  );

  it("keeps an unfamiliar backend option while recovering missing anchors", () => {
    const ask = {
      options: [
        {
          option_id: "vendor-special",
          label: "Do the unusual thing",
          option_kind: "vendor_special"
        }
      ]
    };

    const actions = askActions(ask);

    expect(actions.map((action) => action.act === "cancel_turn" ? "cancel" : action.optionId))
      .toEqual(["cancel", "reject_once", "vendor-special", "allow_once"]);
    expect(actions[2]).toMatchObject({
      act: "answer",
      optionId: "vendor-special",
      label: "Do the unusual thing",
      supplied: true
    });
    expect(askIsGeneric(ask)).toBe(true);
  });

  it("distinguishes permission, question, and shapeless asks", () => {
    expect(askShape({
      options: [
        { option_id: "reject", label: "Decline", option_kind: "reject_once" },
        { option_id: "allow", label: "Approve", option_kind: "allow_once" }
      ]
    })).toBe("permission");
    expect(askShape({
      options: [
        { option_id: "rewrite", label: "Rewrite it", option_kind: "choice" },
        { option_id: "leave", label: "Leave it", option_kind: "choice" }
      ]
    })).toBe("question");
    expect(askShape({ options: [] })).toBe("shapeless");
    expect(askShape(null)).toBe("shapeless");
  });

  it("keeps every question choice and gives digits only to the first nine", () => {
    const ask = {
      options: Array.from({ length: 11 }, (_unused, index) => ({
        option_id: `choice-${index + 1}`,
        label: `Choice ${index + 1}`,
        option_kind: "choice"
      }))
    };

    const choices = askQuestionChoices(ask);

    expect(choices).toHaveLength(11);
    expect(choices.slice(0, 9).map((choice) => choice.shortcutDigit))
      .toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(choices.slice(9).map((choice) => choice.shortcutDigit)).toEqual([null, null]);
    expect(askChoiceForDigit(ask, 3)).toMatchObject({
      optionId: "choice-3",
      label: "Choice 3"
    });
  });

  it.each([0, 10, Number.NaN, 2.5])(
    "does not select a question choice for invalid digit %s",
    (digit) => {
      const ask = {
        options: [{ option_id: "one", label: "One", option_kind: "choice" }]
      };
      expect(askChoiceForDigit(ask, digit)).toBeNull();
    }
  );

  it.each([
    [{ title: "Run ls", detail: "in /workspace" }, "in /workspace"],
    [{ title: "Which way?", detail: '{"questions":[{"q":"a"}]}' }, "Which way?"],
    [{ title: "Run it", detail: "line one\nline two" }, "Run it"],
    [{ title: "Run it", detail: "x".repeat(200) }, "Run it"],
    [{ title: "Run ls", detail: null }, "Run ls"],
    [{ title: "Run ls" }, "Run ls"],
    [null, ""]
  ] as const)("uses a readable one-line ask placeholder", (ask, expected) => {
    expect(askPlaceholder(ask)).toBe(expected);
  });
});
