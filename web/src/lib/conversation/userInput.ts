import type {
  UserInputAnswers,
  UserInputAttachment,
  UserInputQuestion
} from "./wire";
import { pendingFilesAsPieces, type PendingConversationFile } from "./pendingFiles";
import { pendingImagesAsPieces, type PendingConversationImage } from "./pendingImages";

export function pendingAnswerAttachments(
  images: readonly PendingConversationImage[],
  files: readonly PendingConversationFile[]
): UserInputAttachment[] {
  return [...pendingImagesAsPieces(images), ...pendingFilesAsPieces(files)].filter(
    (piece): piece is UserInputAttachment => piece.piece === "image" || piece.piece === "file"
  );
}

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
  questions: readonly UserInputQuestion[],
  attachmentsByQuestion: Record<string, UserInputAttachment[]> = {}
): UserInputAnswers | null {
  const answers: UserInputAnswers = {};
  for (const question of questions) {
    const given = userInputAnswerFor(draft, question);
    const attachments = attachmentsByQuestion[question.question_id] ?? [];
    if (given.length === 0 && (!question.allow_other || attachments.length === 0)) return null;
    if (attachments.length && !question.allow_other) return null;
    answers[question.question_id] = { answers: given, ...(attachments.length ? { attachments } : {}) };
  }
  return answers;
}

/** The option a number key picks in a question (1–9), or null. */
export function userInputOptionForKey(
  question: Pick<UserInputQuestion, "options"> | undefined,
  key: string
): string | null {
  if (!/^[1-9]$/.test(key) || question === undefined) return null;
  return question.options[Number(key) - 1]?.label ?? null;
}

/** The person's own answer, written in the composer while a question waits. The composer is
 *  the one place to type: its words are the "something else" answer to the question on
 *  screen. On a single-answer question they replace a chosen option; on a multi-answer one
 *  they join the chosen ones. Clearing the words takes the own answer back. */
export function withOwnAnswer(
  draft: UserInputDraft,
  question: UserInputQuestion,
  words: string
): UserInputDraft {
  const written = words.trim() !== "";
  return {
    selected: written && !question.multi_select
      ? { ...draft.selected, [question.question_id]: [] }
      : draft.selected,
    other: { ...draft.other, [question.question_id]: words }
  };
}
