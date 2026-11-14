import type { ReadSql } from '../core/database/contracts.ts';

/** A trial keeps copied Tickets visible without scheduling work for them. */
export function automatic_ticket_admitted(
  sql: ReadSql,
  ticket_id: string,
  created_after?: number,
): boolean {
  return (
    created_after === undefined ||
    sql.get('SELECT 1 FROM tickets WHERE id=? AND created_at>=?', [ticket_id, created_after]) !==
      undefined
  );
}
