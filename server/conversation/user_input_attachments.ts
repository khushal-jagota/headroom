/** Resolve answer attachments only where the native backend can read the managed files. */
import { UserInputAnswer } from './events.ts';
import type { ConversationMessageFiles } from './message_files.ts';
import { UserInputAnswerWriteFailed } from './backends/contracts.ts';

export function answer_file_references(
  answers: readonly UserInputAnswer[],
  files: ConversationMessageFiles,
  conversation_id: string,
): readonly UserInputAnswer[] {
  return answers.map((answer) => {
    if (!answer.attachments?.length) return answer;
    try {
      const references = answer.attachments
        .map(
          (piece) =>
            `Attached file ${JSON.stringify(piece.file_name ?? piece.stored_file_id)} (${piece.media_type}) is available at ${files.path_of(conversation_id, piece.stored_file_id)}.`,
        )
        .join('\n');
      const values = [...answer.answers];
      const last = values.pop() ?? '';
      values.push([last, references].filter(Boolean).join('\n\n'));
      return new UserInputAnswer({ question_id: answer.question_id, answers: values });
    } catch (error) {
      throw new UserInputAnswerWriteFailed('An answer attachment could not be read.', {
        cause: error,
      });
    }
  });
}
