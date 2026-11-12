/** Validate one complete answer map, then retain the backend question order. */
import type { UserInputAnswer, UserInputQuestion } from '../events.ts';

export function ordered_user_input_answers(
  questions: readonly UserInputQuestion[],
  answers: readonly UserInputAnswer[],
): readonly UserInputAnswer[] | null {
  const by_id = new Map(answers.map((answer) => [answer.question_id, answer]));
  if (
    by_id.size !== answers.length ||
    by_id.size !== new Set(questions.map((question) => question.question_id)).size ||
    questions.some((question) => !by_id.has(question.question_id))
  )
    return null;
  const ordered = questions.map((question) => by_id.get(question.question_id)!);
  for (let index = 0; index < questions.length; index += 1) {
    const question = questions[index];
    const answer = ordered[index];
    if (
      answer.answers.length === 0 ||
      answer.answers.some((value) => !value) ||
      (!question.multi_select && answer.answers.length !== 1) ||
      (!question.allow_other &&
        answer.answers.some((value) => !question.options.some((option) => option.label === value)))
    )
      return null;
  }
  return ordered;
}
