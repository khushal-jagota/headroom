/** Durable clocks and grants for exact physical Ticket workspaces. */
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { RETENTION_SECONDS } from './contracts.ts';
import type { WorkspaceIdentity, WorkspaceState } from './contracts.ts';

type StateRow = { ticket_id: string; ended_at: number | null; deleted: number };
function state(row: StateRow): WorkspaceState {
  return { ...row, deleted: Boolean(row.deleted) };
}
export function read_workspace_states(sql: ReadSql): Map<string, WorkspaceState> {
  return new Map(
    sql
      .all<StateRow>('SELECT ticket_id,ended_at,deleted FROM ticket_workspace_lifecycle')
      .map((row) => [row.ticket_id, state(row)]),
  );
}
export function read_workspace_states_for_tickets(
  sql: ReadSql,
  ids: readonly string[],
): WorkspaceState[] {
  if (ids.length === 0) return [];
  return sql
    .all<StateRow>(
      `SELECT ticket_id,ended_at,deleted FROM ticket_workspace_lifecycle WHERE ticket_id IN (${ids.map(() => '?').join(',')})`,
      ids,
    )
    .map(state);
}
export function ensure_workspace_activity(
  sql: TransactionSql,
  ticket_id: string,
  now: number,
): WorkspaceState {
  sql.run(
    'UPDATE ticket_workspace_lifecycle SET ended_at=? WHERE ticket_id=? AND ended_at IS NOT NULL AND deleted=0',
    [now, ticket_id],
  );
  const current = read_workspace_states_for_tickets(sql, [ticket_id])[0];
  if (!current) throw new Error(`Unknown Ticket workspace: ${ticket_id}`);
  return current;
}
export function register_workspace(
  sql: TransactionSql,
  ticket_id: string,
  conversation_id: string,
  identity: WorkspaceIdentity,
): void {
  sql.run(
    'INSERT INTO ticket_workspace_conversations (conversation_id,ticket_id,machine_id,workspace_id) VALUES(?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET machine_id=excluded.machine_id,workspace_id=excluded.workspace_id',
    [conversation_id, ticket_id, identity.machine_id, identity.workspace_id],
  );
  sql.run(
    'DELETE FROM ticket_workspace_retirements WHERE ticket_id=? AND machine_id=? AND workspace_id=?',
    [ticket_id, identity.machine_id, identity.workspace_id],
  );
}
export function conversation_workspace_retired(sql: ReadSql, conversation_id: string): boolean {
  return (
    sql.get(
      'SELECT 1 FROM ticket_workspace_conversations w JOIN ticket_workspace_retirements r USING(ticket_id,machine_id,workspace_id) WHERE w.conversation_id=?',
      [conversation_id],
    ) !== undefined
  );
}
export function has_workspace_activity(sql: ReadSql, ticket_id: string): boolean {
  return (
    sql.get(
      `SELECT 1 FROM conversations c JOIN json_each(c.identity_environment_variables) owner
    ON json_extract(owner.value,'$[0]')='PLAN_TICKET_ID'
    WHERE json_extract(owner.value,'$[1]')=? AND (EXISTS(
    SELECT 1 FROM conversation_lifecycle_facts f WHERE f.conversation_id=c.conversation_id
    AND ((f.kind='turn' AND f.status IN ('intent','running','unknown'))
    OR (f.kind='attempt' AND f.status IN ('intent','unknown'))
    OR (f.kind='request' AND f.status IN ('pending','answering')))) OR EXISTS(
    SELECT 1 FROM conversation_background_tasks b WHERE b.conversation_id=c.conversation_id)) LIMIT 1`,
      [ticket_id],
    ) !== undefined
  );
}
export function authorize_retirement(
  sql: TransactionSql,
  expected: WorkspaceState,
  now: number,
  identity: WorkspaceIdentity,
): boolean {
  const current = read_workspace_states_for_tickets(sql, [expected.ticket_id])[0];
  if (
    !current ||
    current.ended_at !== expected.ended_at ||
    current.deleted !== expected.deleted ||
    expected.ended_at === null ||
    now < expected.ended_at + RETENTION_SECONDS
  )
    return false;
  if (
    sql.get("SELECT 1 FROM tickets WHERE id=? AND worker_step_claim='out'", [
      expected.ticket_id,
    ]) !== undefined ||
    has_workspace_activity(sql, expected.ticket_id)
  )
    return false;
  sql.run(
    'INSERT OR IGNORE INTO ticket_workspace_retirements (ticket_id,machine_id,workspace_id,authorized_at) VALUES(?,?,?,?)',
    [expected.ticket_id, identity.machine_id, identity.workspace_id, now],
  );
  return true;
}
export function has_committed_retirement(
  sql: ReadSql,
  expected: WorkspaceState,
  identity: WorkspaceIdentity,
): boolean {
  return (
    sql.get(
      'SELECT 1 FROM ticket_workspace_retirements WHERE ticket_id=? AND machine_id=? AND workspace_id=?',
      [expected.ticket_id, identity.machine_id, identity.workspace_id],
    ) !== undefined
  );
}
