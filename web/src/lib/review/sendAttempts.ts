import { recallOutgoingMessages, rememberOutgoingMessages, type OutgoingMessage } from '../conversation/outgoing';

import type { PendingAttachment } from '../conversation/videoUploads/contracts';

export type ReviewReplyAttempt = {
  message: OutgoingMessage;
  conversationId: string | null;
  localRefusal?: string;
  /** Present until the files are ready and the complete request is durably saved. */
  pendingUploads?: PendingAttachment[];
};

type AttemptMetadata = Omit<ReviewReplyAttempt, 'message'> & {
  messageId: string;
  /** An upload-only draft has no wire pieces until transfer finishes. */
  attachmentOnlyMessage?: OutgoingMessage;
  /** Match the immutable body if reload finds both metadata versions. */
  bodyUploadIds?: string[];
};

function bodyUploadIds(message: OutgoingMessage): string[] {
  return message.content.flatMap(piece => piece.piece === 'attachment_upload' || piece.piece === 'video_upload' ? [piece.upload_id] : []);
}

function metadataFor(value: unknown, id: string): value is AttemptMetadata {
  return !!value && typeof value === 'object' && 'messageId' in value && 'conversationId' in value
    && value.messageId === id && (value.conversationId === null || typeof value.conversationId === 'string');
}

function attemptMetadata(raw: string | null): unknown[] {
  if (raw === null) return [];
  const stored: unknown = JSON.parse(raw);
  return Array.isArray(stored) ? stored : [stored];
}

export async function recallReviewAttempts(
  payloadKey: string, metadataKey: string, legacyConversationId?: string
): Promise<ReviewReplyAttempt[]> {
  const metadata = attemptMetadata(window.sessionStorage.getItem(metadataKey));
  const messages = await recallOutgoingMessages(payloadKey);
  const stored = new Map(messages.map(message => [message.messageId, message]));
  const attempts: ReviewReplyAttempt[] = [];
  const seen = new Set<string>();
  for (const value of metadata) {
    if (!value || typeof value !== 'object' || !('messageId' in value) || typeof value.messageId !== 'string') continue;
    const id = value.messageId;
    if (seen.has(id) || !metadataFor(value, id)) continue;
    const pendingUploads = Array.isArray(value.pendingUploads) ? value.pendingUploads.map(upload => ({
      ...upload, file: undefined, previewUrl: undefined, localVideoUrl: undefined,
      phase: upload.uploadId ? 'validating' as const : 'interrupted' as const
    })) : undefined;
    const message = stored.get(id) ?? (pendingUploads?.length ? value.attachmentOnlyMessage : undefined);
    if (!message) continue;
    if (value.bodyUploadIds && JSON.stringify(value.bodyUploadIds) !== JSON.stringify(bodyUploadIds(message))) continue;
    seen.add(id);
    stored.delete(id);
    attempts.push({
      message, conversationId: value.conversationId,
      localRefusal: typeof value.localRefusal === 'string' ? value.localRefusal : undefined,
      ...(pendingUploads ? { pendingUploads } : {})
    });
  }
  if (stored.size && legacyConversationId === undefined) {
    throw new Error('A saved message has no confirmed destination. Open Chief to review it.');
  }
  return [...attempts, ...[...stored.values()].map(message => ({ message, conversationId: legacyConversationId! }))];
}

export async function rememberReviewAttempts(
  payloadKey: string, metadataKey: string, attempts: ReviewReplyAttempt[]
): Promise<boolean> {
  const previous = window.sessionStorage.getItem(metadataKey);
  const next = attempts.map(({ message, pendingUploads, ...metadata }) => ({
    messageId: message.messageId, ...metadata,
    pendingUploads: pendingUploads?.map(({ file: _file, previewUrl: _previewUrl, localVideoUrl: _localVideoUrl, ...upload }) => upload),
    attachmentOnlyMessage: message.content.length === 0 ? message : undefined,
    bodyUploadIds: bodyUploadIds(message)
  }));
  // Either payload list still has its metadata if reload interrupts the storage write.
  window.sessionStorage.setItem(metadataKey, JSON.stringify([...next, ...attemptMetadata(previous)]));
  const payloads = attempts.map(attempt => attempt.message).filter(message => message.content.length > 0);
  const saved = await rememberOutgoingMessages(payloadKey, payloads);
  const restored = saved ? await recallOutgoingMessages(payloadKey) : [];
  if (!saved || restored.length !== payloads.length
    || restored.some((message, index) => message.messageId !== payloads[index].messageId)) {
    if (previous === null) window.sessionStorage.removeItem(metadataKey);
    else window.sessionStorage.setItem(metadataKey, previous);
    return false;
  }
  if (next.length) window.sessionStorage.setItem(metadataKey, JSON.stringify(next));
  else window.sessionStorage.removeItem(metadataKey);
  return true;
}
