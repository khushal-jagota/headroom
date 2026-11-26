/** Which machine runs a conversation. Every server-side check that needs it asks here. */
import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { canonical_principal } from '../agent_identity/data.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import {
  can_execute_machine,
  machine_never_joins_refusal,
  machine_unavailable_reason,
} from '../tickets/locale_configuration.ts';
import { read_machine } from './data.ts';
import { judge_presence } from '../executor/logic/presence.ts';
import { read_presence } from '../executor/vps_presence_store.ts';

/** A Ticket's conversation follows its Ticket. Any other conversation keeps its recorded machine. */
export function conversation_machine_id(sql: ReadSql, conversation_id: string): string | null {
  const principal = canonical_principal(sql, conversation_id);
  if (principal?.kind === 'ticket')
    return (
      sql.get<{ machine_id: string }>('SELECT machine_id FROM tickets WHERE id=?', [principal.id])
        ?.machine_id ?? null
    );
  return (
    sql.get<{ machine_id: string }>(
      'SELECT machine_id FROM conversations WHERE conversation_id=?',
      [conversation_id],
    )?.machine_id ?? null
  );
}

/**
 * The machine of the Chief's or a Manager's next conversation: the machine of the conversation
 * it replaces, which is its current one or else its latest. A first start runs on the home.
 */
export function agent_machine_id(
  sql: ReadSql,
  agent: { kind: 'chief' } | { kind: 'manager'; id: string },
): string {
  const agent_key = agent.kind === 'chief' ? 'chief_of_staff' : `manager_${agent.id}`;
  const selected = sql.get<{ machine_id: string | null }>(
    'SELECT machine_id FROM agents WHERE agent_key=?',
    [agent_key],
  )?.machine_id;
  if (selected != null) return selected;
  const current = sql.get<{ conversation_id: string | null }>(
    'SELECT conversation_id FROM agents WHERE agent_key=?',
    [agent_key],
  )?.conversation_id;
  if (current != null) {
    const machine = conversation_machine_id(sql, current);
    if (machine !== null) return machine;
  }
  const identity = (name: string) =>
    "(SELECT json_extract(value,'$[1]') FROM json_each(CASE WHEN json_valid(identity_environment_variables) " +
    "THEN identity_environment_variables ELSE '[]' END) WHERE json_extract(value,'$[0]')=" +
    `'${name}' LIMIT 1)`;
  const latest =
    agent.kind === 'chief'
      ? sql.get<{ machine_id: string }>(
          `SELECT machine_id FROM conversations WHERE ${identity('PLAN_ACTOR')}='chief' ORDER BY created_at DESC,rowid DESC LIMIT 1`,
        )
      : sql.get<{ machine_id: string }>(
          "SELECT c.machine_id FROM conversation_history_catalog h JOIN conversations c USING(conversation_id) WHERE h.owner_kind='manager' AND h.owner_id=? ORDER BY c.created_at DESC,c.rowid DESC LIMIT 1",
          [agent.id],
        );
  return latest?.machine_id ?? HOME_MACHINE_ID;
}

/**
 * A conversation that is not a Ticket's waits while its machine's code cannot run it, as a
 * Ticket does. A Ticket's conversation is judged by the Ticket execution policy instead.
 */
export function conversation_machine_refusal(
  sql: ReadSql,
  conversation_id: string,
): PlannerError | null {
  if (canonical_principal(sql, conversation_id)?.kind === 'ticket') return null;
  const machine_id = conversation_machine_id(sql, conversation_id);
  if (machine_id === null) return null;
  const never = machine_never_joins_refusal(machine_id, sql, { conversation_id });
  if (never !== null) return never;
  if (can_execute_machine(machine_id, sql)) return null;
  return new PlannerError('validation', machine_unavailable_reason(machine_id, sql), {
    conversation_id,
    machine_id,
  });
}

/**
 * Why messages to this conversation wait, such as "Waiting for Mac 1": its machine never
 * joined, is away, or runs code this home refuses. Null while the machine is present.
 */
export function conversation_machine_waiting_reason(
  sql: ReadSql,
  conversation_id: string,
  now_ms: number,
): string | null {
  const machine_id = conversation_machine_id(sql, conversation_id);
  if (machine_id === null) return null;
  const never = machine_never_joins_refusal(machine_id, sql, {});
  if (never !== null) return never.message;
  const presence = read_presence(sql, machine_id);
  // A version mismatch names both versions, so the owner can see what to update.
  if (presence?.refusal) return machine_unavailable_reason(machine_id, sql);
  if (
    can_execute_machine(machine_id, sql) &&
    judge_presence(presence, now_ms).presence === 'present'
  )
    return null;
  return `Waiting for ${read_machine(sql, machine_id).name}`;
}
