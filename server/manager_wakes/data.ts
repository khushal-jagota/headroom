import { randomUUID } from 'node:crypto';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type {
  FailureNoticeIdentity,
  ProposalNoticeSuppression,
  WakeBatch,
  WakeBatchStatus,
  WakeSourceKind,
} from './contracts.ts';
import { suppression_reason } from './logic/proposal_notices.ts';
import { parked_destination, route_to_holder } from '../tickets/logic/proposal_routing.ts';
export interface Wake {
  manager_id: string | null;
  ticket_id: string | null;
  source_kind: WakeSourceKind;
  source_revision: number;
  summary: string;
  now: number;
  target?: Principal;
  source_conversation_id?: string | null;
  source_identity?: FailureNoticeIdentity | null;
}
export function create_wake(sql: TransactionSql, w: Wake): boolean {
  if (w.target === undefined && w.manager_id === null)
    throw new Error('a notice requires an explicit recipient');
  const target: Principal = w.target ?? { kind: 'manager', id: w.manager_id! };
  const identity = w.source_identity ?? null;
  if ((w.source_kind === 'source_failure') !== (identity !== null))
    throw new Error('source failure notice requires its typed identity');
  if (w.source_kind !== 'source_failure' && w.ticket_id === null)
    throw new Error('ticket notice requires a Ticket');
  if (identity && (identity.recipient.kind !== target.kind || identity.recipient.id !== target.id))
    throw new Error('source notice identity must match the recipient');
  return (
    sql.run(
      'INSERT OR IGNORE INTO manager_wakes(manager_id,ticket_id,source_kind,source_revision,summary,created_at,target_kind,target_id,source_conversation_id,measure_source_id,source_episode,source_responsibility_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
      [
        w.manager_id,
        w.ticket_id,
        w.source_kind,
        w.source_revision,
        w.summary,
        w.now,
        target.kind,
        target.id,
        w.source_conversation_id ?? null,
        identity?.source_id ?? null,
        identity?.episode ?? null,
        identity?.responsibility_revision ?? null,
      ],
    ).changes === 1
  );
}
export function create_proposal_wake(
  sql: TransactionSql,
  w: {
    manager_id: string;
    ticket_id: string;
    proposal_revision: number;
    ticket_title: string;
    proposal_field: string;
    now: number;
    target?: Principal;
  },
): boolean {
  return create_wake(sql, {
    ...w,
    source_kind: 'proposal',
    source_revision: w.proposal_revision,
    summary: `Ticket \`${w.ticket_id}\` (${w.ticket_title}) filed a proposal for \`${w.proposal_field}\`.`,
  });
}
export function create_worker_error_wake(
  sql: TransactionSql,
  w: {
    target: Principal;
    ticket_id: string;
    claim_revision: number;
    ticket_title: string;
    error_reason: string;
    now: number;
  },
): boolean {
  return create_wake(sql, {
    ...w,
    manager_id: w.target.kind === 'manager' ? w.target.id : '',
    source_kind: 'worker_error',
    source_revision: w.claim_revision,
    summary: `Ticket \`${w.ticket_id}\` (${w.ticket_title}) entered the explicit worker-error state: ${w.error_reason}.`,
  });
}

export function create_turn_failure_wake(
  sql: TransactionSql,
  w: {
    target: Principal;
    ticket_id: string;
    ticket_title: string;
    conversation_id: string;
    event_sequence: number;
    error_summary: string;
    now: number;
  },
): boolean {
  return create_wake(sql, {
    ...w,
    manager_id: w.target.kind === 'manager' ? w.target.id : '',
    source_kind: 'turn_failure',
    source_revision: w.event_sequence,
    source_conversation_id: w.conversation_id,
    summary: `Ticket \`${w.ticket_id}\` (${w.ticket_title}) failed: ${w.error_summary}`,
  });
}

interface BatchRow {
  id: number;
  target_kind: Principal['kind'];
  target_id: string;
  sender_message_id: string;
  message: string;
  status: WakeBatchStatus;
  conversation_id: string | null;
  process_token: string | null;
}
interface WakeRow {
  id: number;
  ticket_id: string | null;
  source_kind: WakeSourceKind;
  source_revision: number;
  target_kind: Principal['kind'];
  target_id: string;
  summary: string;
  measure_source_id: string | null;
  source_episode: number | null;
  source_responsibility_revision: number | null;
}
function batch_from_row(row: BatchRow): WakeBatch {
  return {
    id: row.id,
    manager_id: row.target_kind === 'manager' ? row.target_id : '',
    target: { kind: row.target_kind, id: row.target_id },
    sender_message_id: row.sender_message_id,
    message: row.message,
    status: row.status,
    conversation_id: row.conversation_id,
    process_token: row.process_token,
  };
}
export function batches_waiting_for_outcome(sql: ReadSql): WakeBatch[] {
  return sql
    .all<BatchRow>(
      `SELECT * FROM manager_wake_batches
       WHERE status IN ('offering','dispatching','accepted','uncertain') ORDER BY id`,
    )
    .map(batch_from_row);
}
export function recover_accepted_batches_from_other_processes(
  sql: TransactionSql,
  options: { process_token: string; now: number },
): number {
  return sql.run(
    `UPDATE manager_wake_batches SET status='pending',process_token=NULL,
     conversation_id=NULL,updated_at=? WHERE status='accepted'
     AND (process_token IS NULL OR process_token != ?)`,
    [options.now, options.process_token],
  ).changes;
}
export function release_pending_batch(
  sql: TransactionSql,
  batch_id: number,
  options: { process_token: string },
): boolean {
  return (
    sql.run(
      `DELETE FROM manager_wake_batches WHERE id=? AND status='pending' AND process_token=?`,
      [batch_id, options.process_token],
    ).changes === 1
  );
}
export function preserve_interrupted_dispatches(
  sql: TransactionSql,
  options: { process_token: string; now: number },
): number {
  return sql.run(
    `UPDATE manager_wake_batches SET status='uncertain',updated_at=?
     WHERE status IN ('offering','dispatching')
     AND (process_token IS NULL OR process_token != ?)`,
    [options.now, options.process_token],
  ).changes;
}
function compose_message(rows: readonly Pick<WakeRow, 'summary'>[]): string {
  return (
    'Review these new events that need your attention:\n\n' +
    rows.map((row) => `- ${row.summary}`).join('\n') +
    '\n\nRead the canonical source or Ticket state before you act. These events can share one turn.'
  );
}
function obsolete_proposal(sql: ReadSql, row: WakeRow): ProposalNoticeSuppression | null {
  const target: Principal = { kind: row.target_kind, id: row.target_id };
  const ticket = sql.get<{
    pending_proposal: string | null;
    pending_proposal_revision: number;
    ceiling_holder: string;
  }>(
    `SELECT pending_proposal,pending_proposal_revision,ceiling_holder
     FROM tickets WHERE id=?`,
    [row.ticket_id],
  );
  const current =
    ticket === undefined
      ? { ticket_exists: false, pending_revision: null, reviewer: null }
      : {
          ticket_exists: true,
          pending_revision:
            ticket.pending_proposal === null ? null : ticket.pending_proposal_revision,
          reviewer: parked_destination(
            route_to_holder(JSON.parse(ticket.ceiling_holder) as Principal),
          ),
        };
  const reason = suppression_reason({ source_revision: row.source_revision, target, current });
  return reason === null
    ? null
    : {
        wake_id: row.id,
        ticket_id: row.ticket_id,
        source_revision: row.source_revision,
        target,
        current,
        reason,
      };
}
function retire_proposal(
  sql: TransactionSql,
  suppression: ProposalNoticeSuppression,
  options: { sender_message_id: string | null; now: number },
): void {
  const current = suppression.current;
  sql.run(
    `INSERT OR IGNORE INTO manager_wake_suppressions(
     wake_id,ticket_id,source_revision,target_kind,target_id,reason,ticket_exists,
     current_revision,current_target_kind,current_target_id,sender_message_id,created_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
    [
      suppression.wake_id,
      suppression.ticket_id,
      suppression.source_revision,
      suppression.target.kind,
      suppression.target.id,
      suppression.reason,
      Number(current.ticket_exists),
      current.pending_revision,
      current.reviewer?.kind ?? null,
      current.reviewer?.id ?? null,
      options.sender_message_id,
      options.now,
    ],
  );
  sql.run('UPDATE manager_wakes SET closed_at=? WHERE id=? AND closed_at IS NULL', [
    options.now,
    suppression.wake_id,
  ]);
}
/** Read-only source identity check. Collection and failure writers belong to the sources unit. */
function notice_is_current(sql: ReadSql, row: WakeRow): boolean {
  const source = sql.get<{ responsibility_revision: number }>(
    'SELECT responsibility_revision FROM measure_sources WHERE id=?',
    [row.measure_source_id],
  );
  if (!source || source.responsibility_revision !== row.source_responsibility_revision)
    return false;
  return (
    sql.get(
      `SELECT 1 FROM measure_source_failure_episodes
     WHERE source_id=? AND episode=? AND closed_at IS NULL`,
      [row.measure_source_id, row.source_episode],
    ) !== undefined
  );
}
export function admit_notice_batch(
  sql: TransactionSql,
  options: { conversation_id: string; sender_message_id: string | null; now: number },
): readonly [boolean, string | null] {
  const batch = sql.get<{ id: number; status: WakeBatchStatus }>(
    'SELECT id,status FROM manager_wake_batches WHERE conversation_id=? AND sender_message_id=?',
    [options.conversation_id, options.sender_message_id],
  );
  if (!batch) return [false, null];
  if (['uncertain', 'delivered', 'refused'].includes(batch.status))
    throw new Error('historical notice attempt cannot receive dispatch admission');
  if (batch.status === 'discarded') return [true, 'notice_batch_discarded'];
  const rows = sql.all<WakeRow>(
    `SELECT w.* FROM manager_wakes w JOIN manager_wake_batch_members m ON m.wake_id=w.id
     WHERE m.batch_id=? AND w.source_kind IN ('proposal','source_failure')`,
    [batch.id],
  );
  const stale = rows.filter(
    (row) => row.source_kind === 'source_failure' && !notice_is_current(sql, row),
  );
  const obsolete = rows
    .filter((row) => row.source_kind === 'proposal')
    .map((row) => obsolete_proposal(sql, row))
    .filter((value): value is ProposalNoticeSuppression => value !== null);
  if (obsolete.length || stale.length) {
    for (const row of stale)
      sql.run('UPDATE manager_wakes SET closed_at=? WHERE id=?', [options.now, row.id]);
    for (const value of obsolete) retire_proposal(sql, value, options);
    const reasons = new Set<string>(obsolete.map((value) => value.reason));
    if (stale.length) reasons.add('source_failure_changed');
    sql.run("UPDATE manager_wake_batches SET status='discarded',updated_at=? WHERE id=?", [
      options.now,
      batch.id,
    ]);
    return [true, [...reasons].sort().join(',')];
  }
  sql.run(
    `UPDATE manager_wake_batches SET status='dispatching',updated_at=? WHERE id=?
     AND status IN ('pending','offering','accepted')`,
    [options.now, batch.id],
  );
  return [true, null];
}
function retire_obsolete_unsent_proposals(sql: TransactionSql, now: number): void {
  const rows = sql.all<WakeRow>(
    `SELECT w.* FROM manager_wakes w WHERE w.source_kind='proposal' AND w.closed_at IS NULL
     AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members m
     JOIN manager_wake_batches b ON b.id=m.batch_id WHERE m.wake_id=w.id
     AND b.status IN ('pending','offering','dispatching','accepted','uncertain'))`,
  );
  for (const row of rows) {
    const obsolete = obsolete_proposal(sql, row);
    if (obsolete) retire_proposal(sql, obsolete, { sender_message_id: null, now });
  }
}
function retire_obsolete_source_notices(sql: TransactionSql, now: number): void {
  const rows = sql.all<WakeRow>(
    `SELECT w.* FROM manager_wakes w WHERE w.source_kind='source_failure' AND w.closed_at IS NULL
     AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members m
     JOIN manager_wake_batches b ON b.id=m.batch_id WHERE m.wake_id=w.id
     AND b.status IN ('dispatching','accepted','uncertain'))`,
  );
  for (const row of rows) {
    if (!notice_is_current(sql, row))
      sql.run('UPDATE manager_wakes SET closed_at=? WHERE id=?', [now, row.id]);
  }
}
/** The caller uses one silent write transaction for batch construction. */
export function claim_next_batch(
  sql: TransactionSql,
  options: { process_token: string; now: number },
): WakeBatch | null {
  retire_obsolete_unsent_proposals(sql, options.now);
  retire_obsolete_source_notices(sql, options.now);
  const available = `w.closed_at IS NULL AND NOT EXISTS (
    SELECT 1 FROM manager_wake_batch_members m JOIN manager_wake_batches b ON b.id=m.batch_id
    WHERE m.wake_id=w.id AND b.status IN ('pending','offering','dispatching','accepted','uncertain'))
    AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members recent_m
    JOIN manager_wake_batches recent_b ON recent_b.id=recent_m.batch_id
    WHERE recent_m.wake_id=w.id AND recent_b.status IN ('refused','discarded') AND recent_b.updated_at > ?)`;
  const target = sql.get<{ target_kind: Principal['kind']; target_id: string }>(
    `SELECT w.target_kind,w.target_id FROM manager_wakes w WHERE ${available}
     ORDER BY w.created_at,w.id LIMIT 1`,
    [options.now - 5],
  );
  if (!target) return null;
  const rows = sql.all<Pick<WakeRow, 'id' | 'summary'>>(
    `SELECT w.id,w.summary FROM manager_wakes w
     WHERE w.target_kind=? AND w.target_id=? AND ${available} ORDER BY w.created_at,w.id`,
    [target.target_kind, target.target_id, options.now - 5],
  );
  const id = Number(
    sql.run(
      `INSERT INTO manager_wake_batches(manager_id,sender_message_id,message,status,process_token,
     created_at,updated_at,target_kind,target_id) VALUES (?,?,?,'pending',?,?,?,?,?)`,
      [
        target.target_kind === 'manager' ? target.target_id : '',
        `manager_delivery_wake_${randomUUID().replaceAll('-', '')}`,
        compose_message(rows),
        options.process_token,
        options.now,
        options.now,
        target.target_kind,
        target.target_id,
      ],
    ).lastInsertRowid,
  );
  for (const row of rows)
    sql.run('INSERT INTO manager_wake_batch_members(batch_id,wake_id) VALUES (?,?)', [id, row.id]);
  return batch_from_row(sql.get<BatchRow>('SELECT * FROM manager_wake_batches WHERE id=?', [id])!);
}
export function pending_batches(sql: ReadSql): WakeBatch[] {
  return sql
    .all<BatchRow>("SELECT * FROM manager_wake_batches WHERE status='pending' ORDER BY id")
    .map(batch_from_row);
}
interface BatchUpdate {
  conversation_id: string | null;
  process_token: string;
  now: number;
}
export function record_batch_accepted(
  sql: TransactionSql,
  batch_id: number,
  options: BatchUpdate,
): void {
  sql.run(
    `UPDATE manager_wake_batches SET status='accepted',conversation_id=?,process_token=?,updated_at=?
     WHERE id=? AND status='offering'`,
    [options.conversation_id, options.process_token, options.now, batch_id],
  );
}
export function record_batch_uncertain(
  sql: TransactionSql,
  batch_id: number,
  options: BatchUpdate,
): void {
  sql.run(
    `UPDATE manager_wake_batches SET status='uncertain',conversation_id=?,process_token=?,updated_at=?
     WHERE id=? AND status IN ('pending','offering','dispatching','accepted')`,
    [options.conversation_id, options.process_token, options.now, batch_id],
  );
}
export function record_batch_failure(
  sql: TransactionSql,
  batch_id: number,
  options: { status: WakeBatchStatus; conversation_id: string | null; now: number },
): void {
  if (options.status !== 'refused' && options.status !== 'discarded')
    throw new Error('a definite failure must be refused or discarded');
  sql.run(
    `UPDATE manager_wake_batches SET status=?,conversation_id=?,updated_at=?
     WHERE id=? AND status IN ('pending','offering','dispatching','accepted')`,
    [options.status, options.conversation_id, options.now, batch_id],
  );
}
function close_members(sql: TransactionSql, batch_id: number, now: number): void {
  sql.run(
    `UPDATE manager_wakes SET closed_at=? WHERE closed_at IS NULL AND id IN (
     SELECT wake_id FROM manager_wake_batch_members WHERE batch_id=?)`,
    [now, batch_id],
  );
}
export function close_delivered_batch(
  sql: TransactionSql,
  batch_id: number,
  options: { now: number },
): void {
  sql.run(
    `UPDATE manager_wake_batches SET status='delivered',updated_at=?
     WHERE id=? AND status IN ('offering','dispatching','accepted','uncertain')`,
    [options.now, batch_id],
  );
  close_members(sql, batch_id, options.now);
}
export function close_undeliverable_batch(
  sql: TransactionSql,
  batch_id: number,
  options: { now: number },
): void {
  sql.run("UPDATE manager_wake_batches SET status='discarded',updated_at=? WHERE id=?", [
    options.now,
    batch_id,
  ]);
  close_members(sql, batch_id, options.now);
}
export function delivery_outcome_kind(
  sql: ReadSql,
  conversation_id: string,
  sender_message_id: string,
): string | null {
  return (
    sql.get<{ kind: string }>(
      `SELECT kind FROM conversation_events WHERE conversation_id=?
     AND json_extract(payload,'$.sender_message_id')=? AND kind IN
     ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded')
     ORDER BY sequence LIMIT 1`,
      [conversation_id, sender_message_id],
    )?.kind ?? null
  );
}
export function record_batch_offering(
  sql: TransactionSql,
  batch_id: number,
  options: BatchUpdate,
): void {
  sql.run(
    `UPDATE manager_wake_batches SET status='offering',conversation_id=?,process_token=?,updated_at=?
     WHERE id=? AND status='pending'`,
    [options.conversation_id, options.process_token, options.now, batch_id],
  );
}
