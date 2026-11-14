/** Immutable decisions share the transaction that settles their proposal. */
import { createHash } from 'node:crypto';
import { PlannerError, type Principal } from '../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { Ticket } from './contracts.ts';
import { validate_revision_guidance } from './logic/admission.ts';

export function validate_request(
  message: string | null,
  revision: number | null,
  decision_id: string | null,
): void {
  if (message !== null) validate_revision_guidance(message);
  if (revision !== null && (!Number.isInteger(revision) || revision <= 0))
    throw new PlannerError('validation', 'expected_proposal_revision must be a positive integer');
  if (decision_id !== null && (!decision_id.trim() || Array.from(decision_id).length > 200))
    throw new PlannerError('validation', 'decision_id must contain 1 to 200 characters');
}
export function request_json(principal: Principal, values: Record<string, unknown>): string {
  // Python sorts every dictionary level, including the next holder.
  return JSON.stringify(
    { sender_kind: principal.kind, sender_id: principal.id, ...values },
    (_key, value: unknown) =>
      value !== null && typeof value === 'object' && !Array.isArray(value)
        ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
        : value,
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
      "SELECT DISTINCT ticket_id FROM ticket_decision_messages WHERE delivery_status IN ('pending','sending','queued')",
    )
    .map((row) => row.ticket_id);
}
export function recorded_outcome(
  sql: ReadSql,
  conversation_id: string | null,
  sender_message_id: string,
): string | null {
  const row = sql.get<{ kind: string }>(
    "SELECT kind FROM conversation_events WHERE conversation_id=? AND json_extract(payload,'$.sender_message_id')=? AND kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded') ORDER BY sequence DESC LIMIT 1",
    [conversation_id, sender_message_id],
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
