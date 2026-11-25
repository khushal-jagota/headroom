/** Route-owned admission of streamed attachments through the existing upload service. */
import { isDeepStrictEqual } from 'node:util';
import type { Database } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import {
  MessageFile,
  MessageImage,
  type MediaSize,
  type MessageContent,
} from './message_content.ts';
import type { ConversationMessageFiles } from './message_files.ts';
import { conversation_message_content, inline_image_budget, type SentPiece } from './http.ts';
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
import { MAX_CONVERSATION_IMAGE_BYTES } from './file_validation.ts';

export class VideoSendAdmission implements MessageAdmission {
  private readonly service: VideoUploadService;
  private readonly files: ConversationMessageFiles;
  private readonly store: ConversationStorePrimitives;
  private readonly destination: UploadDestination;
  private readonly sent: SentPiece[];
  private readonly sender_message_id: string;
  private readonly recipient: Principal;
  private checked = new Map<string, CheckedVideoUpload>();
  private content_prepared = false;
  private inline_budget = { remaining: MAX_CONVERSATION_IMAGE_BYTES };

  constructor(
    database: Database,
    db_path: string,
    files: ConversationMessageFiles,
    destination: UploadDestination,
    sent: SentPiece[],
    sender_message_id: string,
    recipient: Principal,
    service?: VideoUploadService,
  ) {
    this.service = service ?? new VideoUploadService(database, db_path);
    this.files = files;
    this.store = new ConversationStorePrimitives(database);
    this.destination = destination;
    this.sent = sent;
    this.sender_message_id = sender_message_id;
    this.recipient = recipient;
  }

  async preflight(current_conversation_id: string | null): Promise<MessageDeliveryResult | null> {
    const uploads = this.sent.filter(
      (piece) => piece.piece === 'video_upload' || piece.piece === 'attachment_upload',
    );
    try {
      this.service.database.write((tx) => {
        for (const upload of uploads) {
          if (upload.piece !== 'video_upload' && upload.piece !== 'attachment_upload') continue;
          if (this.checked.has(upload.upload_id))
            throw new HttpError(422, 'An upload can appear only once in a message.');
          this.checked.set(
            upload.upload_id,
            this.service.preflight(
              upload.upload_id,
              { ...this.destination, expected_conversation_id: current_conversation_id },
              this.sender_message_id,
              tx,
            ),
          );
        }
      });
    } catch (error) {
      this.checked.clear();
      if (error instanceof VideoUploadError) throw new HttpError(error.status_code, error.message);
      throw error;
    }
    const prior =
      [...this.checked.values()].find((checked) => checked.conversation_id !== null)
        ?.conversation_id ?? null;
    if (prior === null || prior === current_conversation_id) return null;
    if (
      [...this.checked.values()].some(
        (checked) => checked.conversation_id !== null && checked.conversation_id !== prior,
      )
    )
      throw new HttpError(409, 'Uploads belong to different conversations.');
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
    this.inline_budget = inline_image_budget(this.sent);
    const content = await conversation_message_content(this.files, conversation_id, this.sent, {
      sender_message_id: this.sender_message_id,
      video_admission: this,
    });
    this.content_prepared = true;
    return content;
  }

  abort_before_dispatch(): void {
    if (!this.content_prepared)
      for (const checked of this.checked.values())
        if (checked.conversation_id === null) this.service.finish(checked, 'refused');
  }

  adopt(
    upload_id: string,
    conversation_id: string,
    media_size: MediaSize | null = null,
  ): MessageImage | MessageFile {
    const checked = this.checked.get(upload_id);
    if (!checked) throw new HttpError(409, 'Attachment admission was not checked.');
    return adopt_upload_piece(
      this.service,
      checked,
      conversation_id,
      this.inline_budget,
      media_size,
    );
  }

  finish(fate: PromptDeliveryFate): void {
    if (!this.checked.size) throw new Error('attachment admission was not checked');
    const outcome =
      fate instanceof PromptDeliveryRefused || fate instanceof PromptDeliverySuppressed
        ? 'refused'
        : fate instanceof PromptDeliveryUncertain
          ? 'uncertain'
          : 'accepted';
    for (const checked of this.checked.values()) this.service.finish(checked, outcome);
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
  const videos = sent.filter(
    (piece) => piece.piece === 'video_upload' || piece.piece === 'attachment_upload',
  );
  if (!videos.length) return null;
  if (principal.kind !== 'owner') throw new HttpError(403, 'only the owner can attach files');
  if (!sender_message_id) throw new HttpError(422, 'attachments require sender_message_id');
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

/** Keep small supported rasters native; every other accepted file remains a readable reference. */
export function adopt_upload_piece(
  service: VideoUploadService,
  checked: CheckedVideoUpload,
  conversation_id: string,
  budget = { remaining: MAX_CONVERSATION_IMAGE_BYTES },
  media_size: MediaSize | null = null,
): MessageImage | MessageFile {
  try {
    const kept = service.adopt(checked, conversation_id);
    const status = service.status(checked.upload_id);
    const image = kept.media_type.startsWith('image/') && kept.byte_count <= budget.remaining;
    if (image) budget.remaining -= kept.byte_count;
    // Only a picture or a video has a box to keep. Any other file drops a stray size.
    const size = /^(image|video)\//.test(kept.media_type) ? media_size : null;
    return image
      ? new MessageImage({
          stored_file_id: kept.stored_file_id,
          media_type: kept.media_type,
          file_name: status.filename,
          media_size: size,
        })
      : new MessageFile({
          stored_file_id: kept.stored_file_id,
          media_type: kept.media_type,
          file_name: status.filename,
          byte_count: kept.byte_count,
          media_size: size,
        });
  } catch (error) {
    if (error instanceof VideoUploadError) throw new HttpError(error.status_code, error.message);
    throw error;
  }
}
