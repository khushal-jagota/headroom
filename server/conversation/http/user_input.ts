/** Answer uploads use the same managed files and aggregate limits as ordinary messages. */
import { Fields } from './intake.ts';
import { conversation_message_content, type SentPiece } from '../http.ts';
import { UserInputAnswer } from '../events.ts';
import type { ConversationMessageFiles } from '../message_files.ts';
import type { MessageImage, MessageFile } from '../message_content.ts';

export type SentAnswerAttachment = Extract<SentPiece, { piece: 'image' | 'file' }>;

export function answer_attachments(value: unknown, question_id: string): SentAnswerAttachment[] {
  const outer = new Fields({ attachments: value }, ['body', 'answers', question_id]);
  if (!Array.isArray(value)) {
    outer.issue('list_type', ['attachments'], 'Input should be a valid list', value);
    outer.finish();
    return [];
  }
  const pieces: SentAnswerAttachment[] = [];
  for (const [index, value_piece] of value.entries()) {
    const fields = new Fields(value_piece, ['body', 'answers', question_id, 'attachments', index]);
    const piece = fields.choice('piece', ['image', 'file']);
    const data = fields.text('data');
    const media_type = fields.text('media_type');
    const file_name = fields.text('file_name', piece === 'image', piece === 'file');
    fields.extras(['piece', 'data', 'media_type', 'file_name']);
    fields.finish();
    pieces.push(
      piece === 'file'
        ? { piece, data: data!, media_type: media_type!, file_name: file_name! }
        : { piece: 'image', data: data!, media_type: media_type!, file_name },
    );
  }
  return pieces;
}

export async function keep_answer_attachments(
  files: ConversationMessageFiles,
  conversation_id: string,
  request_id: string,
  operation_id: string | null,
  answers: readonly UserInputAnswer[],
  uploads: ReadonlyMap<string, SentAnswerAttachment[]>,
): Promise<UserInputAnswer[]> {
  const ordered = [...answers].sort((left, right) =>
    left.question_id.localeCompare(right.question_id),
  );
  const sent = ordered.flatMap((answer) => uploads.get(answer.question_id) ?? []);
  if (!sent.length) return [...answers];
  const content = (await conversation_message_content(files, conversation_id, sent, {
    sender_message_id:
      operation_id === null ? null : JSON.stringify(['answer', request_id, operation_id]),
  })) as (MessageImage | MessageFile)[];
  let offset = 0;
  const by_id = new Map<string, (MessageImage | MessageFile)[]>();
  for (const answer of ordered) {
    const count = uploads.get(answer.question_id)?.length ?? 0;
    by_id.set(answer.question_id, content.slice(offset, offset + count));
    offset += count;
  }
  return answers.map(
    (answer) => new UserInputAnswer({ ...answer, attachments: by_id.get(answer.question_id) }),
  );
}
