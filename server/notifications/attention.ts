import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { capture_gate } from '../attention/history.ts';
import { workflow_from_row } from '../worker_types/store.ts';
export interface ConversationAttention {
  unread_message: boolean;
  pending_ask: boolean;
  errored: boolean;
  latest_sequence: number;
  occurred_at: number;
}
export interface ConversationAttentionSnapshot {
  conversation_id: string;
  latest_sequence: number;
  owner_read_through_sequence: number;
  error_acknowledged_through_sequence: number;
  latest_message_sequence: number;
  pending_permission_ask_ids: ReadonlySet<string>;
  pending_user_input_request_ids: ReadonlySet<string>;
  errored: boolean;
  occurred_at: number;
}
export const NOTHING_WAITING: ConversationAttention = {
  unread_message: false,
  pending_ask: false,
  errored: false,
  latest_sequence: 0,
  occurred_at: 0,
};
interface AttentionEvent {
  kind: string;
  ask_id?: string;
  request_id?: string;
  ending?: string;
}
function advance_pending(permission: Set<string>, input: Set<string>, event: AttentionEvent): void {
  if (event.kind === 'turn_ended') {
    permission.clear();
    input.clear();
  } else if (event.kind === 'permission_asked' && event.ask_id != null)
    permission.add(event.ask_id);
  else if (event.kind === 'permission_answered' && event.ask_id != null)
    permission.delete(event.ask_id);
  else if (event.kind === 'user_input_requested' && event.request_id != null)
    input.add(event.request_id);
  else if (
    ['user_input_answered', 'user_input_failed'].includes(event.kind) &&
    event.request_id != null
  )
    input.delete(event.request_id);
}
export function conversation_attention_snapshot(
  sql: ReadSql,
  conversation_id: string,
): ConversationAttentionSnapshot | null {
  const row = sql.get<{
    latest_sequence: number;
    owner_read_through_sequence: number;
    created_at: number;
    error_acknowledged_through_sequence: number;
  }>(
    `SELECT c.latest_sequence,c.owner_read_through_sequence,c.created_at,
      COALESCE(a.through_sequence,0) error_acknowledged_through_sequence
     FROM conversations c LEFT JOIN conversation_error_acknowledgements a
       ON a.conversation_id=c.conversation_id WHERE c.conversation_id=?`,
    [conversation_id],
  );
  if (!row) return null;
  const permission = new Set<string>();
  const input = new Set<string>();
  let latest_message_sequence = 0;
  let errored = false;
  let occurred_at = row.created_at;
  for (const event of sql.all<{
    sequence: number;
    kind: string;
    payload: string;
    created_at: number;
  }>(
    `SELECT sequence,kind,payload,created_at FROM conversation_events
     WHERE conversation_id=? ORDER BY sequence`,
    [conversation_id],
  )) {
    const payload = { ...JSON.parse(event.payload), kind: event.kind } as AttentionEvent;
    occurred_at = Math.max(occurred_at, event.created_at);
    if (event.kind === 'message_to_owner') latest_message_sequence = event.sequence;
    advance_pending(permission, input, payload);
    if (event.sequence > row.error_acknowledged_through_sequence) {
      if (event.kind === 'prompt') errored = false;
      else if (event.kind === 'turn_ended') errored = payload.ending === 'failed';
    }
  }
  return {
    conversation_id,
    latest_sequence: row.latest_sequence,
    owner_read_through_sequence: row.owner_read_through_sequence,
    error_acknowledged_through_sequence: row.error_acknowledged_through_sequence,
    latest_message_sequence,
    pending_permission_ask_ids: permission,
    pending_user_input_request_ids: input,
    errored,
    occurred_at,
  };
}
export function conversation_attention_snapshot_is_current(
  sql: ReadSql,
  snapshot: ConversationAttentionSnapshot,
): boolean {
  const row = sql.get<{
    latest_sequence: number;
    owner_read_through_sequence: number;
    through_sequence: number;
  }>(
    `SELECT c.latest_sequence,c.owner_read_through_sequence,COALESCE(a.through_sequence,0) through_sequence
     FROM conversations c LEFT JOIN conversation_error_acknowledgements a
       ON a.conversation_id=c.conversation_id WHERE c.conversation_id=?`,
    [snapshot.conversation_id],
  );
  return (
    !!row &&
    row.latest_sequence === snapshot.latest_sequence &&
    row.owner_read_through_sequence === snapshot.owner_read_through_sequence &&
    row.through_sequence === snapshot.error_acknowledged_through_sequence
  );
}
export function advance_conversation_attention(
  snapshot: ConversationAttentionSnapshot,
  payloads: readonly AttentionEvent[],
  options: { owner_read_through_sequence: number; occurred_at: number },
): ConversationAttention {
  const permission = new Set(snapshot.pending_permission_ask_ids);
  const input = new Set(snapshot.pending_user_input_request_ids);
  let latest_message_sequence = snapshot.latest_message_sequence;
  let errored = snapshot.errored;
  payloads.forEach((payload, index) => {
    if (payload.kind === 'message_to_owner')
      latest_message_sequence = snapshot.latest_sequence + index + 1;
    advance_pending(permission, input, payload);
    if (payload.kind === 'prompt') errored = false;
    else if (payload.kind === 'turn_ended') errored = payload.ending === 'failed';
  });
  return {
    unread_message: latest_message_sequence > options.owner_read_through_sequence,
    pending_ask: !!(permission.size || input.size),
    errored,
    latest_sequence: snapshot.latest_sequence + payloads.length,
    occurred_at: Math.max(snapshot.occurred_at, options.occurred_at),
  };
}
export function conversation_attention(
  sql: ReadSql,
  conversation_id?: string,
): Map<string, ConversationAttention> {
  const rows = sql.all<{ conversation_id: string; occurred_at: number }>(
    `SELECT c.conversation_id,COALESCE(MAX(e.created_at),c.created_at) occurred_at
     FROM conversations c LEFT JOIN conversation_events e ON e.conversation_id=c.conversation_id
     ${conversation_id === undefined ? '' : 'WHERE c.conversation_id=?'}
     GROUP BY c.conversation_id ORDER BY c.conversation_id`,
    conversation_id === undefined ? [] : [conversation_id],
  );
  const result = new Map<string, ConversationAttention>();
  for (const row of rows) {
    const snapshot = conversation_attention_snapshot(sql, row.conversation_id)!;
    result.set(row.conversation_id, {
      unread_message: snapshot.latest_message_sequence > snapshot.owner_read_through_sequence,
      pending_ask: !!(
        snapshot.pending_permission_ask_ids.size || snapshot.pending_user_input_request_ids.size
      ),
      errored: snapshot.errored,
      latest_sequence: snapshot.latest_sequence,
      occurred_at: row.occurred_at,
    });
  }
  return result;
}
export function record_attention(
  sql: TransactionSql,
  subject_kind: string,
  subject_id: string,
  notification_type: string,
  active: boolean,
  now: number,
  gate = true,
): void {
  const prior = sql.get<{ active: number; generation: number }>(
    `SELECT active,generation FROM notification_attention_state
     WHERE subject_kind=? AND subject_id=? AND notification_type=?`,
    [subject_kind, subject_id, notification_type],
  );
  if (prior && !!prior.active === active) return;
  let generation = prior?.generation ?? 0;
  if (gate && subject_kind === 'ticket' && notification_type === 'awaiting_approval')
    capture_gate(sql, { ticket_id: subject_id, active, now });
  if (active) {
    generation++;
    sql.run(
      `INSERT INTO notification_attention_edges
      (subject_kind,subject_id,notification_type,generation,occurred_at) VALUES (?,?,?,?,?)`,
      [subject_kind, subject_id, notification_type, generation, now],
    );
  }
  sql.run(
    `INSERT INTO notification_attention_state
      (subject_kind,subject_id,notification_type,active,generation) VALUES (?,?,?,?,?)
    ON CONFLICT(subject_kind,subject_id,notification_type)
    DO UPDATE SET active=excluded.active,generation=excluded.generation`,
    [subject_kind, subject_id, notification_type, Number(active), generation],
  );
}
export function capture_ticket_attention(
  sql: TransactionSql,
  id: string,
  now: number,
  options: {
    conversation_result?: ConversationAttention;
    worker_type_definition?: ReturnType<typeof workflow_from_row>;
  } = {},
): void {
  const row = sql.get<{
    id: string;
    stage: string;
    workflow_json: string;
    worker_step_claim: string;
    pending_proposal: string | null;
    ceiling_holder: string;
    conversation_id: string | null;
  }>('SELECT * FROM tickets WHERE id=?', [id]);
  if (!row) return;
  const workflow = options.worker_type_definition ?? workflow_from_row(row);
  const conversation =
    options.conversation_result ??
    (row.conversation_id
      ? conversation_attention(sql, row.conversation_id).get(row.conversation_id)
      : null) ??
    NOTHING_WAITING;
  const flags = {
    awaiting_reply: conversation.unread_message,
    awaiting_answer: conversation.pending_ask,
    // Errored claims outrank proposals. Blockers do not affect these two status predicates.
    awaiting_approval:
      row.worker_step_claim !== 'errored' &&
      row.pending_proposal !== null &&
      JSON.parse(row.ceiling_holder).kind === 'owner',
    assigned: workflow.stages.find((s) => s.id === row.stage)?.ownership_mode === 'user',
    errored: row.worker_step_claim === 'errored' || conversation.errored,
  };
  for (const [kind, active] of Object.entries(flags))
    record_attention(sql, 'ticket', id, kind, active, now);
}
export function capture_conversation_attention(
  sql: TransactionSql,
  conversation_id: string,
  now: number,
  conversation_result?: ConversationAttention,
): void {
  const attention =
    conversation_result ?? conversation_attention(sql, conversation_id).get(conversation_id);
  if (!attention) return;
  for (const row of sql.all<{ id: string }>(
    'SELECT id FROM tickets WHERE conversation_id=? ORDER BY id',
    [conversation_id],
  ))
    capture_ticket_attention(sql, row.id, now, { conversation_result: attention });
  for (const row of sql.all<{ subject_kind: string; subject_id: string }>(
    `SELECT CASE WHEN i.id IS NULL THEN 'agent' ELSE 'manager' END subject_kind,
       COALESCE(i.id,a.agent_key) subject_id FROM agents a
     LEFT JOIN managers i ON a.agent_key='manager_' || i.id
     WHERE a.conversation_id=? AND (a.agent_key='chief_of_staff' OR i.id IS NOT NULL)
     ORDER BY subject_kind,subject_id`,
    [conversation_id],
  )) {
    for (const [kind, active] of [
      ['awaiting_reply', attention.unread_message],
      ['awaiting_answer', attention.pending_ask],
      ['errored', attention.errored],
    ] as const)
      record_attention(sql, row.subject_kind, row.subject_id, kind, active, now);
  }
}
/** Imported-state reconciliation does not create approval-history gates. */
export function reconcile_attention(
  sql: TransactionSql,
  desired: readonly {
    subject_kind: string;
    subject_id: string;
    notification_type: string;
    active: boolean;
    occurred_at: number;
  }[],
): void {
  for (const row of desired)
    record_attention(
      sql,
      row.subject_kind,
      row.subject_id,
      row.notification_type,
      row.active,
      row.occurred_at,
      false,
    );
}
