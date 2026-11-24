import { recallOutgoingMessages, rememberOutgoingMessages, type OutgoingMessage } from '../conversation/outgoing';

export type ReviewReplyAttempt = { message: OutgoingMessage; conversationId: string | null; localRefusal?: string };

type AttemptMetadata = { messageId: string; conversationId: string | null; localRefusal?: string };

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
  return messages.map(message => {
    const entry = metadata.find(candidate => metadataFor(candidate, message.messageId));
    if (metadataFor(entry, message.messageId)) {
      return {
        message, conversationId: entry.conversationId,
        localRefusal: typeof entry.localRefusal === 'string' ? entry.localRefusal : undefined
      };
    }
    if (legacyConversationId !== undefined) return { message, conversationId: legacyConversationId };
    throw new Error('A saved message has no confirmed destination. Open Chief to review it.');
  });
}

export async function rememberReviewAttempts(
  payloadKey: string, metadataKey: string, attempts: ReviewReplyAttempt[]
): Promise<boolean> {
  const previous = window.sessionStorage.getItem(metadataKey);
  const next = attempts.map(({ message, ...metadata }) => ({ messageId: message.messageId, ...metadata }));
  // Either payload list still has its metadata if reload interrupts the storage write.
  window.sessionStorage.setItem(metadataKey, JSON.stringify([...next, ...attemptMetadata(previous)]));
  const saved = await rememberOutgoingMessages(payloadKey, attempts.map(attempt => attempt.message));
  const restored = saved ? await recallOutgoingMessages(payloadKey) : [];
  if (!saved || restored.length !== attempts.length
    || restored.some((message, index) => message.messageId !== attempts[index].message.messageId)) {
    if (previous === null) window.sessionStorage.removeItem(metadataKey);
    else window.sessionStorage.setItem(metadataKey, previous);
    return false;
  }
  if (next.length) window.sessionStorage.setItem(metadataKey, JSON.stringify(next));
  else window.sessionStorage.removeItem(metadataKey);
  return true;
}
