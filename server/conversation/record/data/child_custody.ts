/** Custody writes share the record owner's transaction and immutable turn occurrence. */
import type { ReadSql, TransactionSql } from '../../../core/database/contracts.ts';
import type { LocalChildIdentity } from '../../local_child_contracts.ts';
import type { LocalChildCustodyRow } from '../../local_child_custody_contracts.ts';

export function is_ticket_conversation(sql: ReadSql, conversation_id: string): boolean {
  return (
    sql.get(
      'SELECT 1 FROM ticket_conversations WHERE conversation_id=? UNION ALL SELECT 1 FROM tickets WHERE conversation_id=? UNION ALL SELECT 1 FROM ticket_workspace_conversations WHERE conversation_id=? LIMIT 1',
      [conversation_id, conversation_id, conversation_id],
    ) !== undefined
  );
}

export function read(sql: ReadSql, conversation_id: string): LocalChildCustodyRow | undefined {
  return sql.get<LocalChildCustodyRow>(
    'SELECT * FROM conversation_child_custody WHERE conversation_id=?',
    [conversation_id],
  );
}
export function same(a: LocalChildCustodyRow | undefined, b: LocalChildCustodyRow): boolean {
  return (
    a !== undefined &&
    Object.keys(b).every(
      (key) => a[key as keyof LocalChildCustodyRow] === b[key as keyof LocalChildCustodyRow],
    )
  );
}
export function occurrence_matches(
  sql: ReadSql,
  conversation_id: string,
  occurrence_id: string | null,
  runtime_epoch: string | null,
): boolean {
  const row = read(sql, conversation_id);
  return (
    row !== undefined && row.occurrence_id === occurrence_id && row.runtime_epoch === runtime_epoch
  );
}
export function write_retired(
  tx: TransactionSql,
  conversation_id: string,
  generation: string,
): void {
  tx.run(
    "UPDATE conversation_child_custody SET state='retired' WHERE conversation_id=? AND generation=?",
    [conversation_id, generation],
  );
}
export function identity(row: LocalChildCustodyRow): LocalChildIdentity | null {
  return row.pid !== null && row.birth !== null && row.boot !== null
    ? {
        pid: row.pid,
        birth: row.birth,
        boot: row.boot,
        unit: row.unit,
        supervision_record: row.supervision_record,
      }
    : null;
}
