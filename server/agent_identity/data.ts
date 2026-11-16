import { createHash, randomBytes } from 'node:crypto';
import { CHIEF, PlannerError, type Principal } from '../core/contracts.ts';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import type { AgentIdentity } from './contracts.ts';

/** Saved role materials never establish authority. */
export function canonical_principal(sql: ReadSql, conversation_id: string): Principal | null {
  const ticket = sql.get<{ id: string }>('SELECT id FROM tickets WHERE conversation_id=?', [
    conversation_id,
  ]);
  if (ticket) return { kind: 'ticket', id: ticket.id };
  if (
    sql.get("SELECT 1 FROM agents WHERE agent_key='chief_of_staff' AND conversation_id=?", [
      conversation_id,
    ])
  )
    return CHIEF;
  const manager = sql.get<{ id: string }>(
    "SELECT managers.id FROM managers JOIN agents ON agents.agent_key='manager_' || managers.id WHERE agents.conversation_id=?",
    [conversation_id],
  );
  return manager ? { kind: 'manager', id: manager.id } : null;
}
function token_hash(token: string): string {
  return createHash('sha256').update(token, 'utf8').digest('hex');
}
export function issue_launch_token(database: Database, conversation_id: string): string {
  return database.write((sql) => {
    if (canonical_principal(sql, conversation_id) === null)
      throw new PlannerError('agent_forbidden', 'conversation has no current agent identity');
    const token = randomBytes(32).toString('base64url');
    sql.run(
      'INSERT INTO agent_launch_tokens(token_hash,conversation_id) VALUES (?,?) ON CONFLICT(conversation_id) DO UPDATE SET token_hash=excluded.token_hash',
      [token_hash(token), conversation_id],
    );
    return token;
  });
}
export function authenticate_launch_token(sql: ReadSql, token: string): AgentIdentity | null {
  const row = sql.get<{ conversation_id: string }>(
    'SELECT conversation_id FROM agent_launch_tokens WHERE token_hash=?',
    [token_hash(token)],
  );
  if (!row) return null;
  const principal = canonical_principal(sql, row.conversation_id);
  return principal === null ? null : { principal, conversation_id: row.conversation_id };
}
export function revoke_launch_tokens(database: Database, conversation_id: string): void {
  database.write((sql) =>
    sql.run('DELETE FROM agent_launch_tokens WHERE conversation_id=?', [conversation_id]),
  );
}
