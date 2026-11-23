import { readComposerDraft, writeComposerDraft } from "./composerDraft";
import type {
  UserInputAnswers,
  UserInputAttachment,
  UserInputQuestion
} from "./wire";
import { pendingUploadsAsPieces } from "./videoUploads/pieces";
import type { PendingAttachment } from "./videoUploads/contracts";
import { pendingFilesAsPieces, type PendingConversationFile } from "./pendingFiles";
import { pendingImagesAsPieces, type PendingConversationImage } from "./pendingImages";

export function pendingAnswerAttachments(
  images: readonly PendingConversationImage[],
  files: readonly PendingConversationFile[],
  uploads: readonly PendingAttachment[] = []
): UserInputAttachment[] {
  return [...pendingImagesAsPieces(images), ...pendingFilesAsPieces(files), ...pendingUploadsAsPieces(uploads)].filter(
    (piece): piece is UserInputAttachment => piece.piece === "image" || piece.piece === "file" || piece.piece === "attachment_upload"
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

export type SavedUserInputDraft = {
  requestId: string;
  step: number;
  draft: UserInputDraft;
  attachmentsByQuestion: Record<string, UserInputAttachment[]>;
};
const userInputStorageKey = (conversationId: string, requestId: string) => `structured-answer:${conversationId}:${requestId}`;
export function rememberUserInputDraft(conversationId: string, saved: SavedUserInputDraft): void {
  const attachmentsByQuestion = Object.fromEntries(Object.entries(saved.attachmentsByQuestion).map(([id, pieces]) => [id,
    pieces.filter((piece) => piece.piece === "attachment_upload" || piece.piece === "video_upload")
  ]));
  writeComposerDraft(userInputStorageKey(conversationId, saved.requestId), JSON.stringify({ ...saved, attachmentsByQuestion }));
}
export function recallUserInputDraft(conversationId: string, request: { requestId: string; questions: readonly UserInputQuestion[] }): SavedUserInputDraft | null {
  try {
    const raw = JSON.parse(readComposerDraft(userInputStorageKey(conversationId, request.requestId)));
    if (raw?.requestId !== request.requestId || !raw.draft?.selected || !raw.draft?.other || !raw.attachmentsByQuestion) return null;
    const draft = emptyUserInputDraft();
    const attachmentsByQuestion: Record<string, UserInputAttachment[]> = {};
    for (const question of request.questions) {
      const id = question.question_id;
      draft.selected[id] = Array.isArray(raw.draft.selected[id]) ? raw.draft.selected[id].filter((word: unknown) => typeof word === "string") : [];
      draft.other[id] = typeof raw.draft.other[id] === "string" ? raw.draft.other[id] : "";
      const pieces = raw.attachmentsByQuestion[id];
      attachmentsByQuestion[id] = Array.isArray(pieces) ? pieces.filter((piece) => piece && (piece.piece === "attachment_upload" || piece.piece === "video_upload") && typeof piece.upload_id === "string") : [];
    }
    return { requestId: request.requestId, step: Number.isInteger(raw.step) ? Math.max(0, Math.min(raw.step, request.questions.length - 1)) : 0, draft, attachmentsByQuestion };
  } catch { return null; }
}
export function clearUserInputDraft(conversationId: string, requestId: string): void {
  writeComposerDraft(userInputStorageKey(conversationId, requestId), "");
}

/** A fresh question inherits the current composer draft. A reload restores its exact pager. */
export function initialUserInputDraft(
  request: { requestId: string; questions: readonly UserInputQuestion[] },
  saved: SavedUserInputDraft | null,
  ownWords: string | null,
  ownAttachments: UserInputAttachment[]
): SavedUserInputDraft {
  if (saved?.requestId === request.requestId) return saved;
  const first = request.questions[0];
  return { requestId: request.requestId, step: 0,
    draft: first && first.allow_other && ownWords !== null ? withOwnAnswer(emptyUserInputDraft(), first, ownWords) : emptyUserInputDraft(),
    attachmentsByQuestion: first && first.allow_other && ownAttachments.length ? { [first.question_id]: ownAttachments } : {} };
}

/** Pending file metadata uses the existing upload draft store, under its exact answer. */
export const userInputUploadDraftKey = (composerKey: string, conversationId: string, requestId: string, questionId: string): string =>
  `${composerKey}:conversation:${conversationId}:answer:${requestId}:${questionId}`;
