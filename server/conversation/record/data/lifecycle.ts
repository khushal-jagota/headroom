/** Facts use the write door's scoped capability, never another writable connection. */
import type { ReadSql, TransactionSql } from '../../../core/database/contracts.ts';
import { validate_fact, validate_transition } from '../../logic/lifecycle_transitions.ts';
import type { ConversationTransactionParticipant, LifecycleFact, LifecycleTransition } from '../lifecycle.ts';

export function read_facts(sql: ReadSql, conversation_id: string): readonly LifecycleFact[] {
    return sql.all<LifecycleFact>(
        'SELECT kind,identity,turn_number,operation,status,provider_identity '
        + 'FROM conversation_lifecycle_facts WHERE conversation_id=? ORDER BY kind,identity', [conversation_id]);
}
export function apply_facts(tx: TransactionSql, conversation_id: string, facts: readonly LifecycleFact[]): void {
    for (const fact of facts) {
        validate_fact(fact);
        const previous = read_facts(tx, conversation_id).find(old => old.kind === fact.kind && old.identity === fact.identity);
        if (previous !== undefined) validate_transition(previous, fact);
        tx.run('INSERT INTO conversation_lifecycle_facts '
            + '(conversation_id,kind,identity,turn_number,operation,status,provider_identity) '
            + 'VALUES (?,?,?,?,?,?,?) ON CONFLICT(conversation_id,kind,identity) '
            + 'DO UPDATE SET status=excluded.status',
            [conversation_id, fact.kind, fact.identity, fact.turn_number, fact.operation, fact.status, fact.provider_identity]);
    }
}
export function participate(tx: TransactionSql, participant: ConversationTransactionParticipant | null,
                            candidate: LifecycleTransition): void {
    if (participant === null) return;
    // TypeScript callers can evade a void signature. Reject a promise before commit.
    const result: unknown = participant(tx, candidate);
    if (result !== null && (typeof result === 'object' || typeof result === 'function')
        && typeof (result as { then?: unknown }).then === 'function') {
        if (result instanceof Promise) void result.catch(() => {});
        throw new TypeError('conversation transaction participants must be synchronous');
    }
}
export function retained_run_is_unresolved(sql: ReadSql, conversation_id: string): boolean {
    return sql.get("SELECT 1 FROM mac_runs WHERE conversation_id=? AND stop_state!='closed'", [conversation_id]) !== undefined;
}
