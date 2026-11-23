/** Answer uploads use the same managed files and aggregate limits as ordinary messages. */
import { HttpError } from '../../core/http/errors.ts';
import { canonical_principal } from '../../agent_identity/data.ts';
import { adopt_upload_piece } from '../video_send_admission.ts';
import type { VideoUploadService } from '../video_uploads/service.ts';
import type { CheckedVideoUpload } from '../video_uploads/contracts.ts';
import { Fields } from './intake.ts';
import { conversation_message_content, inline_image_budget, type SentPiece } from '../http.ts';
import { UserInputAnswer } from '../events.ts';
import type { ConversationMessageFiles } from '../message_files.ts';
import type { MessageImage, MessageFile } from '../message_content.ts';

export type SentAnswerAttachment = Exclude<SentPiece, { piece: 'text' }>;

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
    const piece = fields.choice('piece', ['image', 'file', 'attachment_upload', 'video_upload']);
    if (piece === 'attachment_upload' || piece === 'video_upload') {
      const upload_id = fields.text('upload_id');
      fields.extras(['piece', 'upload_id']);
      fields.finish();
      pieces.push({ piece, upload_id: upload_id! });
      continue;
    }
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
  service?: VideoUploadService,
): Promise<{
  answers: UserInputAnswer[];
  finish: (outcome: 'accepted' | 'refused' | 'uncertain') => void;
}> {
  const ordered = [...answers].sort((left, right) =>
    left.question_id.localeCompare(right.question_id),
  );
  const sent = ordered.flatMap((answer) => uploads.get(answer.question_id) ?? []);
  if (!sent.length) return { answers: [...answers], finish: () => {} };
  const checked = new Map<string, CheckedVideoUpload>();
  const references = sent.filter(
    (piece) => piece.piece === 'attachment_upload' || piece.piece === 'video_upload',
  );
  if (references.length && (!service || !operation_id))
    throw new HttpError(422, 'Uploaded answers require a stable operation identity.');
  if (service)
    service.database.write((tx) => {
      const owner =
        canonical_principal(tx, conversation_id) ??
        (tx.get<{ ticket_id: string }>(
          'SELECT ticket_id FROM ticket_conversations WHERE conversation_id=?',
          [conversation_id],
        )
          ? {
              kind: 'ticket',
              id: tx.get<{ ticket_id: string }>(
                'SELECT ticket_id FROM ticket_conversations WHERE conversation_id=?',
                [conversation_id],
              )!.ticket_id,
            }
          : null);
      for (const piece of references) {
        if (piece.piece !== 'attachment_upload' && piece.piece !== 'video_upload') continue;
        if (checked.has(piece.upload_id))
          throw new HttpError(422, 'An upload can appear only once in an answer.');
        const status = service.status(piece.upload_id, tx);
        const destination = status.destination;
        if (
          !(destination.kind === 'conversation' && destination.id === conversation_id) &&
          !(owner && owner.kind === destination.kind && owner.id === destination.id)
        )
          throw new HttpError(409, 'The upload belongs to a different destination.');
        checked.set(
          piece.upload_id,
          service.preflight(
            piece.upload_id,
            { ...destination, expected_conversation_id: conversation_id },
            JSON.stringify(['answer', request_id, operation_id]),
            tx,
          ),
        );
      }
    });
  const finish = (outcome: 'accepted' | 'refused' | 'uncertain') => {
    for (const value of checked.values()) service!.finish(value, outcome);
  };
  const budget = inline_image_budget(sent);
  const content = (await conversation_message_content(files, conversation_id, sent, {
    sender_message_id:
      operation_id === null ? null : JSON.stringify(['answer', request_id, operation_id]),
    video_admission: {
      adopt: (upload_id, id) => adopt_upload_piece(service!, checked.get(upload_id)!, id, budget),
    },
  })) as (MessageImage | MessageFile)[];
  let offset = 0;
  const by_id = new Map<string, (MessageImage | MessageFile)[]>();
  for (const answer of ordered) {
    const count = uploads.get(answer.question_id)?.length ?? 0;
    by_id.set(answer.question_id, content.slice(offset, offset + count));
    offset += count;
  }
  return {
    answers: answers.map(
      (answer) => new UserInputAnswer({ ...answer, attachments: by_id.get(answer.question_id) }),
    ),
    finish,
  };
}
