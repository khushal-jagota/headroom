import type { ReadSql, TransactionSql } from './database/contracts.ts';
import { PlannerError } from './contracts.ts';
import { capture_ticket_attention } from '../notifications/attention.ts';
export const LIVE_BLOCKER_PREDICATE = "blocker.stage != 'done'";
export function touch_blocked_ticket(
  tx: TransactionSql,
  id: string,
  now: number,
  options: {
    capture_attention?: boolean;
  } = {},
): void {
  tx.run('UPDATE tickets SET updated_at = ? WHERE id = ?', [now, id]);
  if (options.capture_attention !== false) capture_ticket_attention(tx, id, now);
}
export function would_create_active_ticket_block_cycle(
  sql: ReadSql,
  blocking: string,
  blocked: string,
): boolean {
  const queue = [blocked],
    seen = new Set(queue);
  for (let i = 0; i < queue.length; i++) {
    const id = queue[i]!;
    if (id === blocking) return true;
    for (const row of sql.all<{
      blocked_ticket_id: string;
    }>(
      "SELECT ticket_blocks.blocked_ticket_id FROM ticket_blocks JOIN tickets blocker ON blocker.id=ticket_blocks.blocking_ticket_id WHERE ticket_blocks.blocking_ticket_id=? AND blocker.stage != 'done' ORDER BY ticket_blocks.blocked_ticket_id",
      [id],
    ))
      if (!seen.has(row.blocked_ticket_id)) {
        seen.add(row.blocked_ticket_id);
        queue.push(row.blocked_ticket_id);
      }
  }
  return false;
}
export function add_ticket_block(
  tx: TransactionSql,
  blocking: string,
  blocked: string,
  now: number,
): void {
  const detail = { blocking_ticket_id: blocking, blocked_ticket_id: blocked };
  if (blocking === blocked)
    throw new PlannerError('ticket_block_invalid', 'a ticket cannot block itself', detail);
  for (const [field, id] of Object.entries(detail))
    if (!tx.get('SELECT 1 FROM tickets WHERE id=?', [id]))
      throw new PlannerError('ticket_block_invalid', `${field} must be an existing ticket`, {
        [field]: id,
      });
  if (
    tx.get<{
      stage: string;
    }>('SELECT stage FROM tickets WHERE id=?', [blocking])!.stage !== 'done' &&
    would_create_active_ticket_block_cycle(tx, blocking, blocked)
  )
    throw new PlannerError(
      'ticket_block_cycle',
      'ticket block would create an active cycle',
      detail,
    );
  try {
    tx.run('INSERT INTO ticket_blocks (blocking_ticket_id,blocked_ticket_id) VALUES (?,?)', [
      blocking,
      blocked,
    ]);
  } catch (error) {
    if (
      typeof error === 'object' &&
      error !== null &&
      'code' in error &&
      String(error.code).startsWith('SQLITE_CONSTRAINT')
    )
      throw new PlannerError(
        'ticket_block_invalid',
        'ticket block already exists or violates a constraint',
        detail,
      );
    throw error;
  }
  touch_blocked_ticket(tx, blocked, now);
}
export function remove_ticket_block(
  tx: TransactionSql,
  blocking: string,
  blocked: string,
  now: number,
): void {
  if (
    !tx.run('DELETE FROM ticket_blocks WHERE blocking_ticket_id=? AND blocked_ticket_id=?', [
      blocking,
      blocked,
    ]).changes
  )
    throw new PlannerError('not_found', 'ticket block not found', {
      blocking_ticket_id: blocking,
      blocked_ticket_id: blocked,
    });
  touch_blocked_ticket(tx, blocked, now);
}
export function blocked_ticket_ids(sql: ReadSql): Set<string> {
  return new Set(
    sql
      .all<{
        blocked_ticket_id: string;
      }>(
        "SELECT DISTINCT b.blocked_ticket_id FROM ticket_blocks b JOIN tickets blocker ON blocker.id=b.blocking_ticket_id WHERE blocker.stage != 'done' ORDER BY b.blocked_ticket_id",
      )
      .map((r) => r.blocked_ticket_id),
  );
}
export function blocker_summary(sql: ReadSql, id: string) {
  const blocked_by = sql
    .all<{
      id: string;
      title: string;
      stage: string;
    }>(
      "SELECT blocker.id,blocker.title,blocker.stage FROM ticket_blocks JOIN tickets blocker ON blocker.id=ticket_blocks.blocking_ticket_id WHERE ticket_blocks.blocked_ticket_id=? ORDER BY blocker.stage != 'done' DESC,blocker.title COLLATE NOCASE,blocker.id",
      [id],
    )
    .map((r) => ({
      ticket_id: r.id,
      title: r.title,
      stage: r.stage,
      active: r.stage !== 'done',
      href: `#/workspace/${r.id}`,
    }));
  return { blocked: blocked_by.some((r) => r.active), blocked_by };
}
