/** Temporary publication source until the real conversation core exists. */
import type { ConversationAttention, SignalDurableFacts } from '../contracts.ts';
import { readAllDurableFacts } from './durable.ts';
import type { CommitStep, TransactionSql } from '../../core/database/contracts.ts';

/** A new value per call prevents a consumer from changing another conversation. */
export function idle_attention(durable: SignalDurableFacts): ConversationAttention {
  return {
    signal: { working: false, message_for_owner: durable.message_for_owner },
    awaiting_answer: false,
    last_turn_failed: durable.last_turn_failed,
  };
}

export type IdlePublicationCallback = (
  tx: TransactionSql,
  conversation_id: string,
  attention: ConversationAttention,
) => undefined;

/** Navigation owns publication storage. This step supplies idle facts in its source transaction. */
export function idle_publication(publish: IdlePublicationCallback): CommitStep {
  return (tx) => {
    for (const { conversation_id, durable } of readAllDurableFacts(tx)) {
      const result: unknown = publish(tx, conversation_id, idle_attention(durable));
      if (
        result !== null &&
        (typeof result === 'object' || typeof result === 'function') &&
        typeof (result as { then?: unknown }).then === 'function'
      ) {
        // Observe an async rejection after this transaction scope expires.
        if (result instanceof Promise) void result.catch(() => {});
        throw new TypeError('idle publication callbacks must be synchronous');
      }
    }
  };
}
