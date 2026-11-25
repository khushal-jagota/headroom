/** Persistent target generations separate admission from slow lifecycle work. */
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
export function recipient_lifecycle(
  sql: ReadSql,
  target: Principal,
): { generation: number; retiring: number } {
  return (
    sql.get<{ generation: number; retiring: number }>(
      'SELECT generation,retiring FROM recipient_lifecycles WHERE recipient_kind=? AND recipient_id=?',
      [target.kind, target.id],
    ) ?? { generation: 0, retiring: 0 }
  );
}
export function require_recipient_available(sql: ReadSql, target: Principal): void {
  if (recipient_lifecycle(sql, target).retiring)
    throw new PlannerError(
      'retry_later',
      'The recipient has an unfinished restart, reset, cleanup, deletion, or move. Retry after that operation finishes.',
    );
}
export function begin_recipient_lifecycle(tx: TransactionSql, target: Principal): number {
  tx.run(
    'INSERT INTO recipient_lifecycles VALUES(?,?,1,1) ON CONFLICT(recipient_kind,recipient_id) DO UPDATE SET generation=generation+1,retiring=1',
    [target.kind, target.id],
  );
  return recipient_lifecycle(tx, target).generation;
}
export function finish_recipient_lifecycle(
  tx: TransactionSql,
  target: Principal,
  generation: number,
): void {
  const result = tx.run(
    'UPDATE recipient_lifecycles SET retiring=0 WHERE recipient_kind=? AND recipient_id=? AND generation=?',
    [target.kind, target.id, generation],
  );
  if (!result.changes)
    throw new PlannerError('retry_later', 'A newer lifecycle operation owns the recipient.');
}
