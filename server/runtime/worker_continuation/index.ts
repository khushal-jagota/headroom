/** Reconcile completed work with the current claim, then use the ordinary conversation door. */
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type { Ticket } from '../../tickets/contracts.ts';
import type { ConversationEventPayload } from '../../conversation/events.ts';
import type { WorkerContinuationSource } from './contracts.ts';
import { continuation_identity, parse_continuation_identity } from './logic/identity.ts';
import { worker_step_blocker } from '../worker_step_readiness.ts';
import { read_ticket, mark_worker_step_claim_errored } from '../../tickets/data/index.ts';
import {
  worker_step_message_id_for_claim,
  is_memory_notice_sender_message_id,
} from '../worker_memory.ts';
import { stage_ownership_mode } from '../../tickets/logic/machine.ts';

export function continuation_blocker(sql: ReadSql, ticket: Ticket): string | null {
  if (
    sql.get(
      "SELECT 1 FROM ticket_blocks b JOIN tickets blocker ON blocker.id=b.blocking_ticket_id WHERE b.blocked_ticket_id=? AND blocker.stage<>'done' LIMIT 1",
      [ticket.id],
    )
  )
    return 'worker_ticket_blocked';
  if (ticket.worker_step_claim !== 'out' || ticket.ticket_status !== 'agent')
    return 'worker_step_changed';
  if (stage_ownership_mode(ticket.stage, { worker_type_definition: ticket.workflow }) !== 'worker')
    return 'worker_stage_not_worker_owned';
  return worker_step_blocker(
    sql,
    { ...ticket, ticket_status: 'empty' },
    { worker_type_definition: ticket.workflow },
  );
}

export function completed_worker_source(
  sql: ReadSql,
  ticket: Ticket,
): WorkerContinuationSource | null {
  const conversation_id = ticket.conversation_id;
  if (conversation_id === null || continuation_blocker(sql, ticket) !== null) return null;
  const turns = sql.all<{ turn_number: number; status: string }>(
    "SELECT turn_number,status FROM conversation_lifecycle_facts WHERE conversation_id=? AND kind='turn' AND status<>'unavailable' ORDER BY turn_number DESC",
    [conversation_id],
  );
  if (turns[0]?.status !== 'completed') return null;
  const endings = sql.all<{ sequence: number; ending: string }>(
    "SELECT sequence,json_extract(payload,'$.ending') ending FROM conversation_events WHERE conversation_id=? AND kind='turn_ended' ORDER BY sequence DESC",
    [conversation_id],
  );
  const original = worker_step_message_id_for_claim(ticket.id, ticket.worker_step_claim_revision);
  const recovery_messages = new Set(
    sql
      .all<{ recovery_sender_message_id: string }>(
        "SELECT x.recovery_sender_message_id FROM deploy_interruption_deliveries x JOIN deploy_interruptions d USING(interruption_id) WHERE d.owner_kind='ticket' AND d.owner_id=? AND x.conversation_id=? AND x.restored_claim_revision=? AND x.status='started' AND x.recovery_sender_message_id IS NOT NULL",
        [ticket.id, conversation_id, ticket.worker_step_claim_revision],
      )
      .map((row) => row.recovery_sender_message_id),
  );
  // End receipts and their lifecycle facts commit together. Walk past upkeep,
  // but never past a failed or interrupted work turn.
  for (const [index, end] of endings.entries()) {
    const turn = turns[index];
    if (turn === undefined || turn.status !== end.ending || end.ending !== 'completed') return null;
    if (
      sql.get(
        "SELECT 1 FROM conversation_lifecycle_facts WHERE conversation_id=? AND operation='cancel' AND turn_number>=? LIMIT 1",
        [conversation_id, turn.turn_number],
      )
    )
      return null;
    if (
      !sql.get(
        "SELECT 1 FROM conversation_lifecycle_facts WHERE conversation_id=? AND turn_number=? AND kind='attempt' AND operation='prompt' AND status='started'",
        [conversation_id, turn.turn_number],
      )
    )
      return null;
    const previous_sequence = endings[index + 1]?.sequence ?? 0;
    const prompts = sql.all<{ sender_message_id: string | null; sender_label: string }>(
      "SELECT json_extract(payload,'$.sender_message_id') sender_message_id,json_extract(payload,'$.sender_label') sender_label FROM conversation_events WHERE conversation_id=? AND kind='prompt' AND sequence<? AND sequence>?",
      [conversation_id, end.sequence, previous_sequence],
    );
    if (
      !prompts.some(
        (prompt) =>
          (prompt.sender_label !== 'Panels' &&
            !is_memory_notice_sender_message_id(prompt.sender_message_id)) ||
          (prompt.sender_message_id !== null && recovery_messages.has(prompt.sender_message_id)),
      )
    )
      continue;
    const work_messages = [original, ...recovery_messages];
    if (
      !work_messages.some((identity) =>
        sql.get(
          "SELECT 1 FROM conversation_events WHERE conversation_id=? AND kind='prompt' AND json_extract(payload,'$.sender_message_id')=? AND sequence<?",
          [conversation_id, identity, end.sequence],
        ),
      )
    )
      return null;
    const source = {
      ticket_id: ticket.id,
      conversation_id,
      claim_revision: ticket.worker_step_claim_revision,
      turn_number: turn.turn_number,
      completed_sequence: end.sequence,
      retry_sequence: 0,
    };
    return { ...source, sender_message_id: continuation_identity(source) };
  }
  return null;
}

export function unsent_worker_continuation(
  sql: ReadSql,
  ticket: Ticket,
): WorkerContinuationSource | null {
  const source = completed_worker_source(sql, ticket);
  if (source === null) return null;
  const attempts = sql.all<{ sequence: number; kind: string; payload: string }>(
    "SELECT sequence,kind,payload FROM conversation_events WHERE conversation_id=? AND substr(json_extract(payload,'$.sender_message_id'),1,?)=? AND kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded') ORDER BY sequence DESC",
    [
      source.conversation_id,
      source.sender_message_id.length - 1,
      source.sender_message_id.slice(0, -1),
    ],
  );
  if (attempts.length) {
    const last = attempts[0]!;
    const payload = JSON.parse(last.payload) as { suppression_reason?: string };
    if (
      last.kind !== 'prompt_discarded' ||
      payload.suppression_reason !== 'worker_continuation_no_longer_ready'
    )
      return null;
    const retry = { ...source, retry_sequence: last.sequence };
    return { ...retry, sender_message_id: continuation_identity(retry) };
  }
  return source;
}

export function admit_worker_continuation(
  tx: TransactionSql,
  conversation_id: string,
  identity: string | null,
  now: number,
): string | null {
  const source = parse_continuation_identity(identity);
  if (source === null) return null;
  if (!tx.get('SELECT 1 FROM tickets WHERE id=?', [source.ticket_id])) return 'worker_step_changed';
  const ticket = read_ticket(tx, source.ticket_id);
  if (
    ticket.conversation_id !== conversation_id ||
    ticket.worker_step_claim !== 'out' ||
    ticket.worker_step_claim_revision !== source.claim_revision
  )
    return 'worker_step_changed';
  if (
    tx.get(
      "SELECT 1 FROM conversation_lifecycle_facts WHERE conversation_id=? AND operation='cancel' AND turn_number>=? LIMIT 1",
      [conversation_id, source.turn_number],
    )
  )
    return 'worker_continuation_stopped';
  const blocker = continuation_blocker(tx, ticket);
  if (blocker !== null) {
    return 'worker_continuation_no_longer_ready';
  }
  if (
    tx.get(
      "SELECT 1 FROM conversation_lifecycle_facts WHERE conversation_id=? AND kind='turn' AND turn_number>? AND status IN ('failed','interrupted') LIMIT 1",
      [conversation_id, source.turn_number],
    )
  )
    return 'worker_continuation_source_changed';
  // A newer running turn is allowed: the queue owns delivery until that turn ends.
  const end = tx.get<{ ending: string }>(
    "SELECT json_extract(payload,'$.ending') ending FROM conversation_events WHERE conversation_id=? AND sequence=? AND kind='turn_ended'",
    [conversation_id, source.completed_sequence],
  );
  return end?.ending === 'completed' ? null : 'worker_continuation_source_changed';
}

export function mark_continuation_error(
  tx: TransactionSql,
  source: Pick<WorkerContinuationSource, 'ticket_id' | 'conversation_id' | 'claim_revision'>,
  now: number,
  error_reason: string,
): void {
  const current = tx.get<{ conversation_id: string | null }>(
    'SELECT conversation_id FROM tickets WHERE id=?',
    [source.ticket_id],
  );
  if (current?.conversation_id !== source.conversation_id) return;
  mark_worker_step_claim_errored(tx, source.ticket_id, {
    expected_claim: 'out',
    expected_claim_revision: source.claim_revision,
    now,
    error_reason,
  });
}

export function settle_worker_continuations(
  tx: TransactionSql,
  conversation_id: string,
  payloads: readonly ConversationEventPayload[],
  now: number,
): void {
  for (const payload of payloads) {
    if (payload.kind !== 'prompt_delivery_refused' && payload.kind !== 'prompt_delivery_uncertain')
      continue;
    const source = parse_continuation_identity(payload.sender_message_id);
    if (source === null) continue;
    mark_continuation_error(
      tx,
      { ...source, conversation_id },
      now,
      payload.kind === 'prompt_delivery_uncertain'
        ? 'continuation delivery uncertain'
        : `continuation refused (${payload.refusal_reason})`,
    );
  }
}
