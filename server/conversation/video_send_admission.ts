/** The route-owned admission of one already-uploaded video. Upload intake belongs to Unit 19. */
import { isDeepStrictEqual } from 'node:util';
import type { Database } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import { MessageFile, type MessageContent } from './message_content.ts';
import type { ConversationMessageFiles } from './message_files.ts';
import { conversation_message_content, type SentPiece } from './http.ts';
import { ConversationStorePrimitives } from './record/data/store.ts';
import {
  PromptEventPayload,
  PromptDeliveryUncertainEventPayload,
  PromptDiscardedEventPayload,
} from './events.ts';
import {
  PromptDeliveryStarted,
  PromptDeliveryInjected,
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
} from './contracts.ts';
import type { PromptDeliveryFate } from './contracts.ts';
import type { MessageAdmission, MessageDeliveryResult } from '../message_delivery/contracts.ts';
import { VideoUploadService } from './video_uploads/service.ts';
import { VideoUploadError } from './video_uploads/contracts.ts';
import type { CheckedVideoUpload, UploadDestination } from './video_uploads/contracts.ts';

export class VideoSendAdmission implements MessageAdmission {
  private readonly service: VideoUploadService;
  private readonly files: ConversationMessageFiles;
  private readonly store: ConversationStorePrimitives;
  private readonly destination: UploadDestination;
  private readonly sent: SentPiece[];
  private readonly sender_message_id: string;
  private readonly recipient: Principal;
  private checked: CheckedVideoUpload | null = null;
  private content_prepared = false;

  constructor(
    database: Database,
    db_path: string,
    files: ConversationMessageFiles,
    destination: UploadDestination,
    sent: SentPiece[],
    sender_message_id: string,
    recipient: Principal,
  ) {
    this.service = new VideoUploadService(database, db_path);
    this.files = files;
    this.store = new ConversationStorePrimitives(database);
    this.destination = destination;
    this.sent = sent;
    this.sender_message_id = sender_message_id;
    this.recipient = recipient;
  }

  async preflight(current_conversation_id: string | null): Promise<MessageDeliveryResult | null> {
    const upload = this.sent.find((piece) => piece.piece === 'video_upload')!;
    if (upload.piece !== 'video_upload') throw new Error('video admission requires a video');
    try {
      this.checked = this.service.preflight(
        upload.upload_id,
        { ...this.destination, expected_conversation_id: current_conversation_id },
        this.sender_message_id,
      );
    } catch (error) {
      if (error instanceof VideoUploadError) throw new HttpError(error.status_code, error.message);
      throw error;
    }
    const prior = this.checked.conversation_id;
    if (prior === null || prior === current_conversation_id) return null;
    const outcome = this.store.sender_message_outcome(prior, this.sender_message_id);
    let fate: PromptDeliveryFate = new PromptDeliveryUncertain();
    if (outcome !== null) {
      const payload = outcome.payload;
      const content = await this.content(prior);
      if (!('content' in payload) || !isDeepStrictEqual(payload.content, content)) {
        throw new HttpError(422, 'sender_message_id already names a different message');
      }
      if (payload instanceof PromptEventPayload) {
        fate =
          payload.mode === 'steer' ? new PromptDeliveryInjected() : new PromptDeliveryStarted();
      } else if (payload instanceof PromptDeliveryUncertainEventPayload) {
        fate = new PromptDeliveryUncertain();
      } else if (payload instanceof PromptDiscardedEventPayload && payload.suppression_reason) {
        fate = new PromptDeliverySuppressed({ reason: payload.suppression_reason });
      } else if ('refusal_reason' in payload) {
        fate = new PromptDeliveryRefused({
          refusal_reason: payload.refusal_reason as ConstructorParameters<
            typeof PromptDeliveryRefused
          >[0]['refusal_reason'],
        });
      }
    }
    this.finish(fate);
    return { recipient: this.recipient, conversation_id: prior, fate };
  }

  async content(conversation_id: string): Promise<MessageContent> {
    const content = await conversation_message_content(this.files, conversation_id, this.sent, {
      sender_message_id: this.sender_message_id,
      video_admission: this,
    });
    this.content_prepared = true;
    return content;
  }

  abort_before_dispatch(): void {
    if (this.checked !== null && !this.content_prepared && this.checked.conversation_id === null) {
      this.service.finish(this.checked, 'refused');
    }
  }

  adopt(conversation_id: string): MessageFile {
    if (this.checked === null) throw new HttpError(409, 'video upload admission was not checked');
    try {
      const kept = this.service.adopt(this.checked, conversation_id);
      const status = this.service.status(this.checked.upload_id);
      return new MessageFile({
        stored_file_id: kept.stored_file_id,
        media_type: kept.media_type,
        file_name: status.filename,
        byte_count: kept.byte_count,
      });
    } catch (error) {
      if (error instanceof VideoUploadError) throw new HttpError(error.status_code, error.message);
      throw error;
    }
  }

  finish(fate: PromptDeliveryFate): void {
    if (this.checked === null) throw new Error('video upload admission was not checked');
    const outcome =
      fate instanceof PromptDeliveryRefused || fate instanceof PromptDeliverySuppressed
        ? 'refused'
        : fate instanceof PromptDeliveryUncertain
          ? 'uncertain'
          : 'accepted';
    this.service.finish(this.checked, outcome);
  }
}

export function video_send_admission(
  database: Database,
  db_path: string,
  files: ConversationMessageFiles,
  sent: SentPiece[],
  sender_message_id: string | null,
  destination: UploadDestination,
  recipient: Principal,
  principal: Principal,
): VideoSendAdmission | null {
  const videos = sent.filter((piece) => piece.piece === 'video_upload');
  if (!videos.length) return null;
  if (principal.kind !== 'owner') throw new HttpError(403, 'only the owner can attach video');
  if (videos.length > 1) throw new HttpError(422, 'a message permits only one video');
  if (!sender_message_id) throw new HttpError(422, 'video requires sender_message_id');
  return new VideoSendAdmission(
    database,
    db_path,
    files,
    destination,
    sent,
    sender_message_id,
    recipient,
  );
}
