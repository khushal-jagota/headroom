import { createHash } from 'node:crypto';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { capture_gate } from '../attention/history.ts';
import { optional_edge_context_json } from './context.ts';
import { workflow_from_row } from '../worker_types/store.ts';
import type { EdgeKey } from './contracts.ts';
// Task alerts describe a current obligation; message and error edges remain facts.
export const TASK_ALERT_IS_CURRENT = `(d.subject_kind!='ticket'
  OR d.notification_type NOT IN ('awaiting_approval','assigned','awaiting_answer')
  OR (EXISTS (SELECT 1 FROM tickets t WHERE t.id=d.subject_id AND t.is_backlogged=0)
    AND EXISTS (SELECT 1 FROM notification_attention_state a
      WHERE a.subject_kind=d.subject_kind AND a.subject_id=d.subject_id
        AND a.notification_type=d.notification_type AND a.active=1 AND a.generation=d.generation)))`;
export function task_alert_is_current(sql: ReadSql, edge: EdgeKey): boolean {
  return !!sql.get<{ current: number }>(
    `SELECT ${TASK_ALERT_IS_CURRENT} AS current FROM
      (SELECT ? AS subject_kind,? AS subject_id,? AS notification_type,? AS generation) d`,
    [edge.subject_kind, edge.subject_id, edge.notification_type, edge.generation],
  )?.current;
}
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
  /** The later of the conversation's creation and its newest event. */
  occurred_at: number;
  /** The newest event's time, or the creation time when there are no events. */
  latest_event_at: number;
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
export interface AttentionEventRecord {
  sequence: number;
  created_at: number;
  payload: AttentionEvent;
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
/** The one fold from events to attention. A replay and an advance both use it. */
function fold_events(
  snapshot: ConversationAttentionSnapshot,
  events: Iterable<AttentionEventRecord>,
): ConversationAttentionSnapshot {
  const permission = new Set(snapshot.pending_permission_ask_ids);
  const input = new Set(snapshot.pending_user_input_request_ids);
  let { latest_sequence, latest_message_sequence, errored, occurred_at, latest_event_at } =
    snapshot;
  let first = latest_sequence === 0;
  for (const { sequence, created_at, payload } of events) {
    latest_sequence = sequence;
    occurred_at = Math.max(occurred_at, created_at);
    latest_event_at = first ? created_at : Math.max(latest_event_at, created_at);
    first = false;
    if (payload.kind === 'message_to_owner') latest_message_sequence = sequence;
    advance_pending(permission, input, payload);
    if (sequence > snapshot.error_acknowledged_through_sequence) {
      if (payload.kind === 'prompt') errored = false;
      else if (payload.kind === 'turn_ended') errored = payload.ending === 'failed';
    }
  }
  return {
    ...snapshot,
    latest_sequence,
    latest_message_sequence,
    pending_permission_ask_ids: permission,
    pending_user_input_request_ids: input,
    errored,
    occurred_at,
    latest_event_at,
  };
}
interface AttentionHead {
  database_file: string;
  latest_sequence: number;
  owner_read_through_sequence: number;
  created_at: number;
  error_acknowledged_through_sequence: number;
  last_kind: string | null;
  /** A digest, so a kept head does not hold a large payload in memory. */
  last_payload_digest: string | null;
  last_created_at: number | null;
}
/** One indexed row: the conversation, its acknowledgement, and its newest event. */
function attention_head(sql: ReadSql, conversation_id: string): AttentionHead | undefined {
  const row = sql.get<Omit<AttentionHead, 'last_payload_digest'> & { last_payload: string | null }>(
    `SELECT (SELECT file FROM pragma_database_list WHERE name='main') database_file,
      c.latest_sequence,c.owner_read_through_sequence,c.created_at,
      COALESCE(a.through_sequence,0) error_acknowledged_through_sequence,
      e.kind last_kind,e.payload last_payload,e.created_at last_created_at
     FROM conversations c LEFT JOIN conversation_error_acknowledgements a
       ON a.conversation_id=c.conversation_id
     LEFT JOIN conversation_events e
       ON e.conversation_id=c.conversation_id AND e.sequence=c.latest_sequence
     WHERE c.conversation_id=?`,
    [conversation_id],
  );
  if (!row) return undefined;
  const { last_payload, ...head } = row;
  return {
    ...head,
    last_payload_digest:
      last_payload === null ? null : createHash('sha256').update(last_payload).digest('base64'),
  };
}
interface KeptAttention {
  snapshot: ConversationAttentionSnapshot;
  head: AttentionHead;
}
/**
 * The last attention state of each conversation this process saw, keyed by database file
 * and conversation. Each use compares it with the database head, so a rolled-back write
 * or another writer causes a replay, not a stale answer.
 */
const kept_attention = new Map<string, KeptAttention>();
function kept_key(head: AttentionHead, conversation_id: string): string {
  return `${head.database_file}\0${conversation_id}`;
}
function same_head(kept: AttentionHead, head: AttentionHead): boolean {
  return (
    kept.latest_sequence === head.latest_sequence &&
    kept.created_at === head.created_at &&
    kept.error_acknowledged_through_sequence === head.error_acknowledged_through_sequence &&
    kept.last_kind === head.last_kind &&
    kept.last_created_at === head.last_created_at &&
    kept.last_payload_digest === head.last_payload_digest
  );
}
/** The full replay from history, without the kept state. */
export function replay_conversation_attention(
  sql: ReadSql,
  conversation_id: string,
  head: AttentionHead | undefined = attention_head(sql, conversation_id),
): ConversationAttentionSnapshot | null {
  if (!head) return null;
  const events = sql.all<{ sequence: number; kind: string; payload: string; created_at: number }>(
    `SELECT sequence,kind,payload,created_at FROM conversation_events
     WHERE conversation_id=? ORDER BY sequence`,
    [conversation_id],
  );
  return fold_events(
    {
      conversation_id,
      latest_sequence: 0,
      owner_read_through_sequence: head.owner_read_through_sequence,
      error_acknowledged_through_sequence: head.error_acknowledged_through_sequence,
      latest_message_sequence: 0,
      pending_permission_ask_ids: new Set(),
      pending_user_input_request_ids: new Set(),
      errored: false,
      occurred_at: head.created_at,
      latest_event_at: head.created_at,
    },
    (function* () {
      // Parse one event at a time, so the parsed history is never all in memory.
      for (const event of events)
        yield {
          sequence: event.sequence,
          created_at: event.created_at,
          payload: { ...JSON.parse(event.payload), kind: event.kind } as AttentionEvent,
        };
    })(),
  );
}
/** Current attention from the kept state, replaying the history only when it is stale. */
export function conversation_attention_snapshot(
  sql: ReadSql,
  conversation_id: string,
): ConversationAttentionSnapshot | null {
  const head = attention_head(sql, conversation_id);
  if (!head) return null;
  const key = kept_key(head, conversation_id);
  const kept = kept_attention.get(key);
  if (kept && same_head(kept.head, head))
    return { ...kept.snapshot, owner_read_through_sequence: head.owner_read_through_sequence };
  const snapshot = replay_conversation_attention(sql, conversation_id, head)!;
  kept_attention.set(key, { snapshot, head });
  return snapshot;
}
function attention_of(snapshot: ConversationAttentionSnapshot): ConversationAttention {
  return {
    unread_message: snapshot.latest_message_sequence > snapshot.owner_read_through_sequence,
    pending_ask: !!(
      snapshot.pending_permission_ask_ids.size || snapshot.pending_user_input_request_ids.size
    ),
    errored: snapshot.errored,
    latest_sequence: snapshot.latest_sequence,
    occurred_at: snapshot.occurred_at,
  };
}
/** Advances a snapshot with the events just written, and keeps the result for the next write. */
export function advance_conversation_attention(
  sql: ReadSql,
  snapshot: ConversationAttentionSnapshot,
  written: readonly AttentionEventRecord[],
  options: { owner_read_through_sequence: number; occurred_at: number },
): ConversationAttention {
  const advanced = {
    ...fold_events(snapshot, written),
    owner_read_through_sequence: options.owner_read_through_sequence,
  };
  const head = attention_head(sql, snapshot.conversation_id);
  if (head && head.latest_sequence === advanced.latest_sequence)
    kept_attention.set(kept_key(head, snapshot.conversation_id), { snapshot: advanced, head });
  return {
    ...attention_of(advanced),
    occurred_at: Math.max(advanced.occurred_at, options.occurred_at),
  };
}
export function conversation_attention(
  sql: ReadSql,
  conversation_id?: string,
): Map<string, ConversationAttention> {
  const ids =
    conversation_id === undefined
      ? sql
          .all<{
            conversation_id: string;
          }>('SELECT conversation_id FROM conversations ORDER BY conversation_id')
          .map((row) => row.conversation_id)
      : [conversation_id];
  const result = new Map<string, ConversationAttention>();
  for (const id of ids) {
    const snapshot = conversation_attention_snapshot(sql, id);
    if (snapshot)
      result.set(id, { ...attention_of(snapshot), occurred_at: snapshot.latest_event_at });
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
      (subject_kind,subject_id,notification_type,generation,occurred_at,context_json) VALUES (?,?,?,?,?,?)`,
      [
        subject_kind,
        subject_id,
        notification_type,
        generation,
        now,
        optional_edge_context_json(sql, subject_kind, subject_id, notification_type),
      ],
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
export function has_live_blocker(sql: ReadSql, id: string): boolean {
  return !!sql.get(
    "SELECT 1 FROM ticket_blocks b JOIN tickets blocker ON blocker.id=b.blocking_ticket_id WHERE b.blocked_ticket_id=? AND blocker.stage != 'done'",
    [id],
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
    is_backlogged: number;
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
    awaiting_answer: conversation.pending_ask && !row.is_backlogged,
    // Errored claims outrank proposals. A live blocker holds a proposal out of the owner's queue.
    awaiting_approval:
      row.worker_step_claim !== 'errored' &&
      !has_live_blocker(sql, id) &&
      row.pending_proposal !== null &&
      JSON.parse(row.ceiling_holder).kind === 'owner' &&
      !row.is_backlogged,
    assigned:
      !row.is_backlogged &&
      workflow.stages.find((s) => s.id === row.stage)?.ownership_mode === 'user',
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
/** Imported-state reconciliation captures newly observed gate transitions too. */
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
    );
}
