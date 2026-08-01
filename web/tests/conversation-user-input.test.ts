import { afterEach, describe, expect, it, vi } from "vitest";

import {
  completeUserInputAnswers,
  emptyUserInputDraft,
  toggleUserInputChoice,
  userInputAnswerFor
} from "../src/lib/conversation/userInput";
import type { UserInputQuestion } from "../src/lib/conversation/wire";
import { answerUserInput } from "../src/lib/conversation/wire";

const single: UserInputQuestion = {
  question_id: "framework",
  header: "Framework",
  question: "Which framework?",
  options: [
    { label: "Svelte", description: "Keep the current stack" },
    { label: "React", description: "Move to React" }
  ],
  multi_select: false,
  allow_other: true
};

const multiple: UserInputQuestion = {
  question_id: "checks",
  header: "Checks",
  question: "Which checks should run?",
  options: [
    { label: "Unit", description: "Fast checks" },
    { label: "Browser", description: "Whole-flow checks" }
  ],
  multi_select: true,
  allow_other: true
};

describe("structured user-input drafts", () => {
  it("keeps one answer for single choice and clears it when Other is typed", () => {
    let draft = toggleUserInputChoice(emptyUserInputDraft(), single, "Svelte");
    draft = toggleUserInputChoice(draft, single, "React");
    expect(userInputAnswerFor(draft, single)).toEqual(["React"]);

    draft = {
      selected: { ...draft.selected, framework: [] },
      other: { framework: "  Vue  " }
    };
    expect(userInputAnswerFor(draft, single)).toEqual(["Vue"]);
  });

  it("toggles multiple choices and preserves a typed Other answer", () => {
    let draft = toggleUserInputChoice(emptyUserInputDraft(), multiple, "Unit");
    draft = toggleUserInputChoice(draft, multiple, "Browser");
    draft = toggleUserInputChoice(draft, multiple, "Unit");
    draft = { ...draft, other: { checks: "Lint" } };

    expect(userInputAnswerFor(draft, multiple)).toEqual(["Browser", "Lint"]);
  });

  it("submits the complete question-id answer map only when every question is answered", () => {
    let draft = toggleUserInputChoice(emptyUserInputDraft(), single, "Svelte");
    expect(completeUserInputAnswers(draft, [single, multiple])).toBeNull();

    draft = toggleUserInputChoice(draft, multiple, "Unit");
    expect(completeUserInputAnswers(draft, [single, multiple])).toEqual({
      framework: { answers: ["Svelte"] },
      checks: { answers: ["Unit"] }
    });
  });
});

describe("structured user-input wire", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("posts the complete answer map to the question endpoint", async () => {
    const fetch = vi.fn(async () =>
      new Response(JSON.stringify({ landed: true }), {
        status: 200,
        headers: { "content-type": "application/json" }
      })
    );
    vi.stubGlobal("fetch", fetch);

    await expect(answerUserInput("conversation/one", "input-1", {
      framework: { answers: ["Svelte"] },
      checks: { answers: ["Unit", "Browser"] }
    })).resolves.toEqual({ landed: true });
    expect(fetch).toHaveBeenCalledWith(
      "/api/conversation/conversations/conversation%2Fone/user-input-answers",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: "input-1",
          answers: {
            framework: { answers: ["Svelte"] },
            checks: { answers: ["Unit", "Browser"] }
          }
        })
      }
    );
  });
});
