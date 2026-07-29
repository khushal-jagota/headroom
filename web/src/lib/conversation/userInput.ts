import type {
  UserInputAnswers,
  UserInputQuestion
} from "./wire";

export type UserInputDraft = {
  selected: Record<string, string[]>;
  other: Record<string, string>;
};

export function emptyUserInputDraft(): UserInputDraft {
  return { selected: {}, other: {} };
}

export function toggleUserInputChoice(
  draft: UserInputDraft,
  question: UserInputQuestion,
  label: string
): UserInputDraft {
  const current = draft.selected[question.question_id] ?? [];
  const next = question.multi_select
    ? current.includes(label)
      ? current.filter((answer) => answer !== label)
      : [...current, label]
    : [label];
  return {
    selected: { ...draft.selected, [question.question_id]: next },
    other: question.multi_select
      ? draft.other
      : { ...draft.other, [question.question_id]: "" }
  };
}

export function userInputAnswerFor(
  draft: UserInputDraft,
  question: UserInputQuestion
): string[] {
  const selected = draft.selected[question.question_id] ?? [];
  const other = (draft.other[question.question_id] ?? "").trim();
  if (!other) return selected;
  return question.multi_select ? [...selected, other] : [other];
}

export function completeUserInputAnswers(
  draft: UserInputDraft,
  questions: readonly UserInputQuestion[]
): UserInputAnswers | null {
  const answers: UserInputAnswers = {};
  for (const question of questions) {
    const given = userInputAnswerFor(draft, question);
    if (given.length === 0) return null;
    answers[question.question_id] = { answers: given };
  }
  return answers;
}
