import { expect, it, vi } from 'vitest';
import { mintOutgoingMessage, rememberOutgoingMessages, resetOutgoingImageReservationsForTest } from '../src/lib/conversation/outgoing';
import { chiefAttemptKey, chiefAttemptMetadataKey } from '../src/lib/review/chiefMessage';
import { recallReviewAttempts, rememberReviewAttempts } from '../src/lib/review/sendAttempts';

// A tab can carry the former Chief destination format across an upgrade. Preserve
// the first send's null destination and keep a definite local refusal editable after reload.
it('keeps a legacy Chief destination and a definite refusal through storage', async () => {
  const values = new Map<string, string>();
  const storage = {
    get length() { return values.size; },
    key: (index: number) => [...values.keys()][index] ?? null,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key)
  };
  vi.stubGlobal('window', { sessionStorage: storage });
  resetOutgoingImageReservationsForTest();
  try {
    const original = mintOutgoingMessage({ content: [{ piece: 'text', text: 'First message' }], senderLabel: 'owner', mode: 'queue' });
    storage.setItem(chiefAttemptMetadataKey, JSON.stringify({ messageId: original.messageId, conversationId: null }));
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
