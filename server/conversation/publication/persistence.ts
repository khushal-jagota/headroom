/** Producer-private state on the caller's ordinary source transaction. */
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type {
  ConversationSignalPublicationV1,
  SignalAssociationEffect,
  SignalCursor,
} from '../contracts.ts';

export function encode(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(encode).join(',') + ']';
  if (value !== null && typeof value === 'object') {
    const object = value as Record<string, unknown>;
    return (
      '{' +
      Object.keys(object)
        .sort()
        .map((key) => JSON.stringify(key) + ':' + encode(object[key]))
        .join(',') +
      '}'
    );
  }
  return JSON.stringify(value);
}

export function cursor(sql: ReadSql): SignalCursor {
  const row = sql.get<SignalCursor>(`
    SELECT scope, runtime_epoch, sequence
    FROM signal_publication_meta WHERE singleton=1
  `);
  if (!row) throw new Error('Signal producer is not initialized');
  return row;
}

export function values(sql: ReadSql): readonly ConversationSignalPublicationV1[] {
  return sql
    .all<{ value: string }>(
      `
    SELECT value FROM signal_publication_values
    WHERE value IS NOT NULL ORDER BY conversation_id
  `,
    )
    .map((row) => JSON.parse(row.value) as ConversationSignalPublicationV1);
}

export function associations(sql: ReadSql): readonly SignalAssociationEffect[] {
  return sql
    .all<{ value: string }>(
      `
    SELECT value FROM signal_publication_associations ORDER BY sequence
  `,
    )
    .map((row) => JSON.parse(row.value) as SignalAssociationEffect);
}

export function mark_changed(tx: TransactionSql, conversation_id: string): void {
  tx.run('INSERT OR IGNORE INTO signal_publication_dirty VALUES(?)', [conversation_id]);
}
