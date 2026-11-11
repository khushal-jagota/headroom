/** Temporary publication source until the real conversation core exists. */
import type { ConversationAttention } from '../contracts.ts';
import type { CommitStep, TransactionSql } from '../../core/database/contracts.ts';

/** A new value per call prevents a consumer from changing another conversation. */
export function idle_attention(): ConversationAttention {
    return {signal: {working: false, message_for_owner: false}, awaiting_answer: false, last_turn_failed: false};
}

export type IdlePublicationCallback = (tx: TransactionSql, conversation_id: string, attention: ConversationAttention) => undefined;

/** Navigation owns publication storage. This step supplies idle facts in its source transaction. */
export function idle_publication(publish: IdlePublicationCallback): CommitStep {
    return tx => {
        for (const row of tx.all<{conversation_id: string}>('SELECT conversation_id FROM conversations ORDER BY conversation_id')) {
            const result: unknown = publish(tx, row.conversation_id, idle_attention());
            if (result !== null && (typeof result === 'object' || typeof result === 'function')
                && typeof (result as {then?: unknown}).then === 'function') {
                // Observe an async rejection after this transaction scope expires.
                if (result instanceof Promise) void result.catch(() => {});
                throw new TypeError('idle publication callbacks must be synchronous');
            }
        }
    };
}
