import { expect, it, vi } from 'vitest';
import { mintOutgoingMessage, rememberOutgoingMessages, resetOutgoingImageReservationsForTest } from '../src/lib/conversation/outgoing';
import { chiefAttemptKey, chiefAttemptMetadataKey } from '../src/lib/review/chiefMessage';
import { recallReviewAttempts, rememberReviewAttempts } from '../src/lib/review/sendAttempts';
import { reviewReplyOutcome } from '../src/lib/review/replyOutcome';
import { ownerSendOutcomeAsDelivery } from '../src/lib/conversation/ownerSend';
import type { OwnerSendOutcome } from '../src/lib/conversation/wire';

function useTabStorage() {
  const values = new Map<string, string>();
  const storage = {
    get length() { return values.size; },
    key: (index: number) => [...values.keys()][index] ?? null,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key)
  };
  vi.stubGlobal('window', { sessionStorage: storage });
  return values;
}

// A tab can carry the former Chief destination format across an upgrade. Preserve
// the first send's null destination and keep a definite local refusal editable after reload.
it('keeps a legacy Chief destination and a definite refusal through storage', async () => {
  const values = useTabStorage();
  resetOutgoingImageReservationsForTest();
  try {
    const original = mintOutgoingMessage({ content: [{ piece: 'text', text: 'First message' }], senderLabel: 'owner', mode: 'queue' });
    values.set(chiefAttemptMetadataKey, JSON.stringify({ messageId: original.messageId, conversationId: null }));
    await rememberOutgoingMessages(chiefAttemptKey, [original]);
    const [legacy] = await recallReviewAttempts(chiefAttemptKey, chiefAttemptMetadataKey);
    expect(legacy).toMatchObject({ conversationId: null, message: { messageId: original.messageId, content: original.content } });

    const rejected = { ...legacy, localRefusal: 'Panels turned this reply away.' };
    expect(await rememberReviewAttempts(chiefAttemptKey, chiefAttemptMetadataKey, [rejected])).toBe(true);
    expect(await recallReviewAttempts(chiefAttemptKey, chiefAttemptMetadataKey)).toEqual([rejected]);
  } finally {
    resetOutgoingImageReservationsForTest();
  }
});

// A message can be submitted while its only file still uploads. The existing wire
// outbox cannot decode an empty payload, so its metadata must retain recovery.
it('reopens an attachment-only message without saving its browser file or preview', async () => {
  const values = useTabStorage();
  resetOutgoingImageReservationsForTest();
  try {
    const message = mintOutgoingMessage({ content: [], senderLabel: 'owner', mode: 'queue' });
    const conversationId = 'review-chief';
    const attachment = {
      id: 1, fileName: 'notes.txt', byteCount: 11, mediaType: 'text/plain',
      phase: 'uploading' as const, receivedBytes: 1, uploadId: 'staged-token',
      destination: { kind: 'chief' as const, id: 'chief', expected_conversation_id: conversationId },
      file: new File(['private bytes'], 'notes.txt'), previewUrl: 'blob:do-not-store'
    };
    expect(await rememberReviewAttempts(chiefAttemptKey, chiefAttemptMetadataKey, [{
      message, conversationId, pendingUploads: [attachment]
    }])).toBe(true);
    expect(await recallReviewAttempts(chiefAttemptKey, chiefAttemptMetadataKey)).toMatchObject([{
      message, conversationId,
      pendingUploads: [{ fileName: 'notes.txt', uploadId: 'staged-token', phase: 'validating' }]
    }]);
    const savedMetadata = values.get(chiefAttemptMetadataKey);
    expect(savedMetadata).not.toContain('"file":');
    expect(savedMetadata).not.toContain('blob:');

    // Reload between the new metadata write and the wire body write. That body never
    // became sendable. The saved upload must survive, even though its staged metadata
    // mentions a complete request.
    const committed = JSON.parse(savedMetadata!);
    values.set(chiefAttemptMetadataKey, JSON.stringify([
      { messageId: message.messageId, conversationId, bodyUploadIds: ['staged-token'] },
      ...committed
    ]));
    expect(await recallReviewAttempts(chiefAttemptKey, chiefAttemptMetadataKey)).toMatchObject([{
      message, pendingUploads: [{ fileName: 'notes.txt', uploadId: 'staged-token' }]
    }]);
  } finally {
    resetOutgoingImageReservationsForTest();
  }
});

// A send answers at acceptance. Only a recorded reply may tick and finish the card; a
// pending one keeps it open, and a later refusal or uncertainty ends it with its words.
it('keeps an accepted reply waiting until its outcome read settles it', () => {
  expect(reviewReplyOutcome({ fate: 'accepted', message_id: 'm', conversation_id: 'c' }, 'sending'))
    .toEqual({ status: 'accepted', note: 'Accepted for delivery' });
  const read = (outcome: OwnerSendOutcome['outcome'], fields: Partial<OwnerSendOutcome> = {}) => reviewReplyOutcome(
    ownerSendOutcomeAsDelivery({ message_id: 'm', conversation_id: 'c', outcome, refusal_reason: null, reason: null, ...fields }),
    'accepted');
  expect(read('pending')?.status).toBe('accepted');
  expect(read('delivered')?.status).toBe('sent');
  expect(read('refused', { refusal_reason: 'model_resolution_failed' }))
    .toEqual({ status: 'failed', note: 'Not delivered. The selected model is unavailable.' });
  expect(read('uncertain')).toEqual({ status: 'uncertain', note: 'The agent did not confirm receipt.' });
  expect(read('discarded', { reason: 'automatic_compaction_child_stopped' })).toBeNull();
  // A reply restored after a reload is read again from "checking". It must settle the same way.
  expect(reviewReplyOutcome(ownerSendOutcomeAsDelivery({ message_id: 'm', conversation_id: 'c', outcome: 'discarded',
    refusal_reason: null, reason: null }), 'checking')).toBeNull();
  expect(reviewReplyOutcome({ fate: 'suppressed', reason: 'proposal_revision_changed', conversation_id: 'c' }, 'sending'))
    .toEqual({ status: 'failed', note: 'not sent · proposal_revision_changed' });
});
