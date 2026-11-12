import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { read_ticket } from '../tickets/data/index.ts';
import {
  PreviewError,
  type PreviewRecord,
  type ProcessIdentity,
  type TicketEligibility,
} from './contracts.ts';
export const TABLE = 'ticket_application_previews';
export function eligibility(sql: ReadSql, ticket_id: string): TicketEligibility {
  let ticket;
  try {
    ticket = read_ticket(sql, ticket_id);
  } catch {
    throw new PreviewError('This Ticket no longer exists.', 410);
  }
  const row = sql.get<{ folder_path: string | null }>(
    'SELECT folder_path FROM projects WHERE id=?',
    [ticket.project_id],
  );
  return {
    ticket_id,
    project_folder: row?.folder_path || null,
    terminal: ticket.workflow.stages.find((s) => s.id === ticket.stage)!.is_terminal,
    locale: ticket.locale,
  };
}
type PreviewRow = Omit<PreviewRecord, 'process'> & {
  boot_id: string | null;
  pid: number | null;
  start_ticks: number | null;
  listener_inode: number | null;
  port: number | null;
};
export function from_row(row: PreviewRow): PreviewRecord {
  return {
    ticket_id: row.ticket_id,
    generation: row.generation,
    worktree: row.worktree,
    public_origin: row.public_origin,
    server_epoch: row.server_epoch,
    created_at: row.created_at,
    state: row.state,
    ended_reason: row.ended_reason,
    process:
      row.pid === null
        ? null
        : {
            boot_id: row.boot_id!,
            pid: row.pid,
            start_ticks: row.start_ticks!,
            listener_inode: row.listener_inode!,
            port: row.port!,
            worktree: row.worktree,
          },
  };
}
export function get(sql: ReadSql, ticket_id: string, generation?: string): PreviewRecord | null {
  const row =
    generation === undefined
      ? sql.get<PreviewRow>(
          `SELECT * FROM ${TABLE} WHERE ticket_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1`,
          [ticket_id],
        )
      : sql.get<PreviewRow>(`SELECT * FROM ${TABLE} WHERE ticket_id=? AND generation=?`, [
          ticket_id,
          generation,
        ]);
  return row ? from_row(row) : null;
}
export const live = (sql: ReadSql): PreviewRecord[] =>
  sql.all<PreviewRow>(`SELECT * FROM ${TABLE} WHERE state!='ended'`).map(from_row);
export function end(tx: TransactionSql, generation: string, reason: string): void {
  tx.run(`UPDATE ${TABLE} SET state='ended',ended_reason=? WHERE generation=? AND state!='ended'`, [
    reason,
    generation,
  ]);
}
export function prepare(tx: TransactionSql, record: PreviewRecord): void {
  tx.run(
    `UPDATE ${TABLE} SET state='ended',ended_reason='Replaced by a new preparation.' WHERE ticket_id=? AND state!='ended'`,
    [record.ticket_id],
  );
  tx.run(
    `INSERT INTO ${TABLE} (generation,ticket_id,worktree,public_origin,server_epoch,created_at,state) VALUES(?,?,?,?,?,?,?)`,
    [
      record.generation,
      record.ticket_id,
      record.worktree,
      record.public_origin,
      record.server_epoch,
      record.created_at,
      record.state,
    ],
  );
}
export function register(
  tx: TransactionSql,
  record: PreviewRecord,
  process: ProcessIdentity,
): void {
  let changes;
  try {
    changes = tx.run(
      `UPDATE ${TABLE} SET state='active',boot_id=?,pid=?,start_ticks=?,listener_inode=?,port=? WHERE generation=? AND state='prepared' AND server_epoch=?`,
      [
        process.boot_id,
        process.pid,
        process.start_ticks,
        process.listener_inode,
        process.port,
        record.generation,
        record.server_epoch,
      ],
    ).changes;
  } catch (error) {
    if (String((error as { code?: string }).code).startsWith('SQLITE_CONSTRAINT'))
      throw new PreviewError('That listener is already registered to another Ticket.');
    throw error;
  }
  if (changes !== 1)
    throw new PreviewError('This preparation has ended; prepare a new address.', 410);
}
