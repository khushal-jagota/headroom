/** Indexed facts about diagnostic source sequences omitted by maintenance. */
import type { ReadSql } from '../../core/database/contracts.ts';

export function diagnostics_expired(sql: ReadSql, conversation_id: string): boolean {
    return sql.get('SELECT 1 FROM conversation_expired_ranges WHERE conversation_id=? LIMIT 1', [conversation_id]) !== undefined;
}

export function sequence_expired(sql: ReadSql, conversation_id: string, sequence: number): boolean {
    const row = sql.get<{ last_sequence: number }>(
        'SELECT last_sequence FROM conversation_expired_ranges '
        + 'WHERE conversation_id=? AND first_sequence<=? ORDER BY first_sequence DESC LIMIT 1',
        [conversation_id, sequence],
    );
    return row !== undefined && sequence <= row.last_sequence;
}
