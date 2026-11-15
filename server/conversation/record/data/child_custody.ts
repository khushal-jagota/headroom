/** Custody writes share the record owner's transaction and immutable turn occurrence. */
import type { ReadSql, TransactionSql } from '../../../core/database/contracts.ts';
import type { LocalChildIdentity, LocalHostBootIdentity } from '../../local_child_contracts.ts';
import type { LocalChildCustodyRow } from '../../local_child_custody_contracts.ts';

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
export function write_never_started(tx: TransactionSql, conversation_id: string): void {
  tx.run(
    "INSERT INTO conversation_child_custody(conversation_id,generation,state) VALUES (?,'never-started','retired')",
    [conversation_id],
  );
  bind_current_turn(tx, conversation_id);
}
export function bind_current_turn(tx: TransactionSql, conversation_id: string): void {
  const turn = tx.get<{ occurrence_id: string; runtime_epoch: string }>(
    'SELECT occurrence_id,runtime_epoch FROM conversation_turn_occurrences WHERE conversation_id=? ORDER BY turn_number DESC LIMIT 1',
    [conversation_id],
  );
  const epoch =
    turn?.runtime_epoch ??
    tx.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1')
      ?.epoch;
  tx.run(
    'UPDATE conversation_child_custody SET occurrence_id=?,runtime_epoch=? WHERE conversation_id=?',
    [turn?.occurrence_id ?? null, epoch ?? null, conversation_id],
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
export function write_pending(
  tx: TransactionSql,
  conversation_id: string,
  generation: string,
  current: LocalHostBootIdentity,
): void {
  tx.run(
    "INSERT INTO conversation_child_custody(conversation_id,generation,state,host,boot) VALUES (?,?,'launch_pending',?,?) ON CONFLICT(conversation_id) DO UPDATE SET generation=excluded.generation,state=excluded.state,pid=NULL,birth=NULL,boot=excluded.boot,host=excluded.host,unit=NULL,supervision_record=NULL",
    [conversation_id, generation, current.host, current.boot],
  );
  bind_current_turn(tx, conversation_id);
}
export function write_supervision(
  tx: TransactionSql,
  conversation_id: string,
  generation: string,
  path: string,
): void {
  if (
    tx.run(
      "UPDATE conversation_child_custody SET supervision_record=? WHERE conversation_id=? AND generation=? AND state='launch_pending'",
      [path, conversation_id, generation],
    ).changes !== 1
  )
    throw new Error('Worker supervision record lost pending custody');
}
export function write_started(
  tx: TransactionSql,
  conversation_id: string,
  generation: string,
  identity: LocalChildIdentity,
): void {
  if (
    !Number.isInteger(identity.pid) ||
    identity.pid <= 0 ||
    !identity.birth.trim() ||
    !identity.boot.trim() ||
    (identity.unit !== null && !/^panels-worker-[0-9a-f]{32}\.service$/.test(identity.unit))
  )
    return;
  tx.run(
    "UPDATE conversation_child_custody SET state='started',pid=?,birth=?,boot=?,unit=?,supervision_record=? WHERE conversation_id=? AND generation=? AND state='launch_pending' AND host IS NOT NULL AND host!='' AND boot=?",
    [
      identity.pid,
      identity.birth,
      identity.boot,
      identity.unit,
      identity.supervision_record,
      conversation_id,
      generation,
      identity.boot,
    ],
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
