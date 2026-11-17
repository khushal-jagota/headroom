/** Immutable decisions share the transaction that settles their proposal. */
import { createHash } from 'node:crypto';
import { PlannerError, type Principal } from '../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { Ticket } from './contracts.ts';
import { validate_revision_guidance } from './logic/admission.ts';
import { stringify_python_json } from '../core/http/numeric_json.ts';

export function validate_request(
  message: string | null,
  revision: number | bigint | null,
  decision_id: string | null,
): void {
  if (message !== null) validate_revision_guidance(message);
  if (
    revision !== null &&
    ((typeof revision !== 'bigint' && !Number.isInteger(revision)) || revision <= 0)
  )
    throw new PlannerError('validation', 'expected_proposal_revision must be a positive integer');
  if (decision_id !== null && (!decision_id.trim() || Array.from(decision_id).length > 200))
    throw new PlannerError('validation', 'decision_id must contain 1 to 200 characters');
}
export function request_json(principal: Principal, values: Record<string, unknown>): string {
  // Stable decision fingerprints sort every object level, including the next holder.
  return stringify_python_json(
    { sender_kind: principal.kind, sender_id: principal.id, ...values },
    { sort_keys: true, compact: true, ensure_ascii: false },
  );
}
export function is_replay(sql: ReadSql, decision_id: string | null, request: string): boolean {
  if (decision_id === null) return false;
  const row = sql.get<{ request_json: string }>(
    'SELECT request_json FROM ticket_decision_messages WHERE decision_id=?',
    [decision_id],
  );
  if (!row) return false;
  if (row.request_json !== request)
    throw new PlannerError('validation', 'decision_id already names a different decision');
  return true;
}
export function actor_name(sql: ReadSql, principal: Principal): string {
  if (principal.kind === 'owner') return 'User';
  if (principal.kind === 'chief') return 'Chief';
  if (principal.kind === 'manager') {
    const row = sql.get<{ title: string }>('SELECT title FROM managers WHERE id=?', [principal.id]);
    return row ? `Manager ${row.title}` : 'Manager';
  }
  return 'Worker';
}
export function save(
  sql: TransactionSql,
  ticket: Ticket,
  options: {
    action: string;
    field: string;
    principal: Principal;
    message: string | null;
    decision_id: string | null;
    request: string;
    now: number;
  },
): void {
  const identity =
    options.decision_id ??
    `legacy:${ticket.id}:${ticket.pending_proposal_revision}:${options.action}`;
  const label =
    ticket.workflow.fields.find((value) => value.id === options.field)?.label ?? options.field;
  const text =
    `${actor_name(sql, options.principal)} ${options.action} the ${label} proposal` +
    (options.message ? ` with this note:\n${options.message}` : '.');
  const sender_message_id =
    'ticket_decision_' + createHash('sha256').update(identity).digest('hex');
  sql.run(
    'INSERT INTO ticket_decision_messages (decision_id,ticket_id,action,field,proposal_revision,sender_kind,sender_id,request_json,message,model_text,created_at,sender_message_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
    [
      identity,
      ticket.id,
      options.action,
      options.field,
      ticket.pending_proposal_revision,
      options.principal.kind,
      options.principal.id,
      options.request,
      options.message,
      text,
      options.now,
      sender_message_id,
    ],
  );
}
export function pending_ticket_ids(sql: ReadSql): string[] {
  return sql
    .all<{ ticket_id: string }>(
      'SELECT DISTINCT ticket_id FROM ticket_decision_messages m ' +
        'WHERE m.rowid=(SELECT first.rowid FROM ticket_decision_messages first ' +
        "WHERE first.ticket_id=m.ticket_id AND first.delivery_status!='delivered' " +
        'ORDER BY first.created_at,first.rowid LIMIT 1) AND ' +
        "((delivery_status='pending' AND NOT EXISTS (SELECT 1 FROM tickets t, json_each(t.workflow_json, '$.stages') stage WHERE t.id=m.ticket_id AND json_extract(stage.value, '$.id')=t.stage AND json_extract(stage.value, '$.is_terminal'))) OR delivery_status IN ('sending','queued') OR " +
        "(delivery_status IN ('uncertain','refused','discarded') AND " +
        '(SELECT kind FROM conversation_events e WHERE e.conversation_id=m.conversation_id ' +
        "AND json_extract(e.payload,'$.sender_message_id')=CASE WHEN instr(m.sender_message_id, '|decision|') > 0 THEN substr(m.sender_message_id,1,instr(m.sender_message_id, '|decision|')-1) ELSE m.sender_message_id END " +
        "AND e.kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded') " +
        "ORDER BY e.sequence DESC LIMIT 1)='prompt'))",
    )
    .map((row) => row.ticket_id);
}
// A combined step has one receipt. Each decision retains a unique durable alias.
export function receipt_sender_message_id(sender_message_id: string): string {
  return sender_message_id.split('|decision|', 1)[0]!;
}
export function recorded_outcome(
  sql: ReadSql,
  conversation_id: string | null,
  sender_message_id: string,
): string | null {
  const row = sql.get<{ kind: string }>(
    "SELECT kind FROM conversation_events WHERE conversation_id=? AND json_extract(payload,'$.sender_message_id')=? AND kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded') ORDER BY sequence DESC LIMIT 1",
    [conversation_id, receipt_sender_message_id(sender_message_id)],
  );
  return row
    ? ({
        prompt: 'delivered',
        prompt_delivery_refused: 'refused',
        prompt_delivery_uncertain: 'uncertain',
        prompt_discarded: 'discarded',
      }[row.kind] ?? null)
    : null;
}
export function rearm_after_restart(sql: TransactionSql, ticket_id: string): void {
  const rows = sql.all<{
    decision_id: string;
    conversation_id: string | null;
    sender_message_id: string;
  }>(
    "SELECT decision_id,conversation_id,sender_message_id FROM ticket_decision_messages WHERE ticket_id=? AND delivery_status!='delivered'",
    [ticket_id],
  );
  for (const row of rows) {
    const delivered =
      recorded_outcome(sql, row.conversation_id, row.sender_message_id) === 'delivered';
    sql.run(
      'UPDATE ticket_decision_messages SET delivery_status=?,conversation_id=? WHERE decision_id=?',
      [
        delivered ? 'delivered' : 'pending',
        delivered ? row.conversation_id : null,
        row.decision_id,
      ],
    );
  }
}
export function has_undelivered(sql: ReadSql, ticket_id: string): boolean {
  return (
    sql.get(
      "SELECT 1 FROM ticket_decision_messages WHERE ticket_id=? AND delivery_status!='delivered' LIMIT 1",
      [ticket_id],
    ) !== undefined
  );
}
