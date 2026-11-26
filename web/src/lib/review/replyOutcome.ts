import { fateSentence } from '../conversation/composer';
import type { DeliveredMessage } from '../conversation/wire';

/** ``accepted`` is a reply home holds with no outcome yet, queued for a busy agent or not.
 *  ``sent`` is a reply the conversation recorded. */
export type ReplyStatus = 'preparing' | 'uploading' | 'upload-failed' | 'waiting' | 'checking' | 'sending'
  | 'connection-failed' | 'failed' | 'uncertain' | 'accepted' | 'sent';

/** What a send answer, or a later outcome read as one, makes of a Review reply.
 *
 * Only a recorded reply is ``sent``, which is what the send button's tick says and what
 * lets the card finish. An accepted or queued reply keeps the card open until its
 * outcome comes. Null takes away a reply that home accepted and then dropped on purpose.
 */
export function reviewReplyOutcome(delivery: DeliveredMessage, current: ReplyStatus):
  { status: ReplyStatus; note: string } | null {
  switch (delivery.fate) {
    case 'accepted': return { status: 'accepted', note: 'Accepted for delivery' };
    case 'queued': return { status: 'accepted', note: 'Queued' };
    case 'started': return { status: 'sent', note: 'Sent' };
    case 'injected': return { status: 'sent', note: 'Sent to the running turn' };
    case 'uncertain': return { status: 'uncertain', note: 'The agent did not confirm receipt.' };
    // A suppression the send itself answered is a failure. One read later, live or after
    // a reload, is a message home accepted and then dropped, which takes the reply away.
    case 'suppressed': if (current !== 'sending') return null;
  }
  return { status: 'failed', note: fateSentence(delivery) ?? 'The reply was not sent.' };
}
