/** An accepted decision owns its uploads across Worker claims and conversation creation. */
import type { SentPiece } from '../conversation/http.ts';
import { adopt_upload_piece } from '../conversation/video_send_admission.ts';
import type { VideoUploadService } from '../conversation/video_uploads/service.ts';
import type { CheckedVideoUpload } from '../conversation/video_uploads/contracts.ts';
import type { MessageFile, MessageImage } from '../conversation/message_content.ts';

export function decision_attachment_admission(
  service: VideoUploadService | undefined,
  ticket_id: string,
  decision_id: string,
  sent: readonly SentPiece[],
  checks: CheckedVideoUpload[],
  budget: { remaining: number },
):
  | { adopt: (upload_id: string, conversation_id: string) => MessageImage | MessageFile }
  | undefined {
  const refs = sent.filter(
    (piece) => piece.piece === 'attachment_upload' || piece.piece === 'video_upload',
  );
  if (!refs.length) return undefined;
  if (!service) throw new Error('Decision attachments require the upload service.');
  const by_id = new Map<string, CheckedVideoUpload>();
  service.database.write((tx) => {
    for (const piece of refs) {
      if (piece.piece !== 'attachment_upload' && piece.piece !== 'video_upload') continue;
      const status = service.status(piece.upload_id, tx);
      if (status.destination.kind !== 'ticket' || status.destination.id !== ticket_id)
        throw new Error('The upload belongs to another Ticket.');
      const checked = service.preflight(
        piece.upload_id,
        status.destination,
        'decision-upload:' + decision_id,
        tx,
      );
      by_id.set(piece.upload_id, checked);
      checks.push(checked);
    }
  });
  return {
    adopt: (upload_id, conversation_id) =>
      adopt_upload_piece(service, by_id.get(upload_id)!, conversation_id, budget),
  };
}
