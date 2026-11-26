/** Answer a send once home holds it durably, while its delivery continues. */
import { Deferred } from '../core/deferred.ts';
import type { TransactionSql } from '../core/database/contracts.ts';
import type { NativeMessageOperation } from '../conversation/contracts.ts';

export interface AcceptanceAnswer<Answer, Delivered> {
  readonly sender_id?: string;
  readonly submission_id: string;
  /** Work that commits with the recipient's custody row. */
  readonly in_admission?: (transaction: TransactionSql, conversation_id: string) => void;
  /** The answer once custody committed, naming the conversation that took it. */
  readonly accepted: (conversation_id: string | null) => Answer;
  /** The answer when the delivery settles before any custody, such as an early refusal. */
  readonly before_acceptance: (delivered: Delivered) => Answer;
  /** Logged when the delivery fails after the sender already has its answer. */
  readonly failure_label: string;
}

export function answer_at_acceptance<Answer, Delivered>(
  deliver: (recording_receipt: NativeMessageOperation) => Promise<Delivered>,
  answer: AcceptanceAnswer<Answer, Delivered>,
): Promise<Answer> {
  const accepted = new Deferred<Answer>();
  let conversation_id: string | null = null;
  const delivery = deliver({
    sender_id: answer.sender_id ?? '',
    submission_id: answer.submission_id,
    in_admission: (transaction, id) => {
      answer.in_admission?.(transaction, id);
      conversation_id = id;
    },
    admitted: () => {
      if (!accepted.settled) accepted.resolve(answer.accepted(conversation_id));
    },
  });
  void delivery.catch((error) => {
    if (!accepted.settled) accepted.reject(error);
    else console.error(answer.failure_label, error);
  });
  return Promise.race([accepted.promise, delivery.then(answer.before_acceptance)]);
}
