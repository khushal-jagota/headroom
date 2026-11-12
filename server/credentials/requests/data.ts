/** Metadata persistence and delivery receipts share the conversation transaction. */
import type { Principal } from '../../core/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { stringify_python_json } from '../../core/http/numeric_json.ts';
import { PromptDeliveryRefusalReason } from '../../conversation/contracts.ts';
import { CredentialRequestEventPayload } from '../../conversation/events.ts';
import type { ConversationEventKind, ConversationEventPayload } from '../../conversation/events.ts';
import { conversation_event_payload_from_canonical_json } from '../../conversation/record/codec.ts';
import type { CredentialRequestRowBody, RequestDeliveryStatus } from './contracts.ts';
import { decode_snapshot } from './logic/validation.ts';

export interface CredentialRequestRow {
  request_id: string;
  conversation_id: string;
  requester_kind: Principal['kind'];
  requester_id: string;
  request_json: string;
  body: string;
  created_at: number;
  updated_at: number;
}
export interface CredentialRequestDeliveryAttemptRow {
  attempt_id: string;
  request_id: string;
  sender_message_id: string;
  prompt: string;
  status: 'intent' | RequestDeliveryStatus;
  refusal_reason: string | null;
  created_at: number;
}
export function encoded(value: unknown): string {
  return stringify_python_json(value, { sort_keys: true, compact: true });
}
export function row_for(sql: ReadSql, request_id: string): CredentialRequestRow {
  const row = sql.get<CredentialRequestRow>(
    'SELECT * FROM credential_requests WHERE request_id=?', [request_id],
  );
  if (row === undefined) throw new PlannerError('not_found', 'credential request not found');
  return row;
}
export function read(sql: ReadSql, request_id: string): CredentialRequestRowBody {
  return decode_snapshot(JSON.parse(row_for(sql, request_id).body));
}
export function update(tx: TransactionSql, body: CredentialRequestRowBody, now: number): CredentialRequestEventPayload {
  tx.run('UPDATE credential_requests SET body=?,updated_at=? WHERE request_id=?', [encoded(body), now, body.request_id]);
  return new CredentialRequestEventPayload({ request: body });
}
export function delivery_status(payload: ConversationEventPayload): readonly [RequestDeliveryStatus, string | null] | null {
  switch (payload.kind) {
    case 'prompt': return ['started', null];
    case 'prompt_delivery_uncertain': return ['uncertain', null];
    case 'prompt_delivery_refused': {
      const reason = payload.refusal_reason;
      return [reason === PromptDeliveryRefusalReason.write_to_backend_failed ? 'uncertain' : 'refused', reason];
    }
    case 'prompt_discarded': return ['canceled', null];
    default: return null;
  }
}
export function settle_attempt(tx: TransactionSql, attempt_id: string, status: RequestDeliveryStatus, reason: string | null, now: number): CredentialRequestEventPayload | null {
  const attempt = tx.get<CredentialRequestDeliveryAttemptRow>(
    'SELECT * FROM credential_request_delivery_attempts WHERE attempt_id=?', [attempt_id],
  );
  if (attempt === undefined || (status === 'queued' && attempt.status !== 'intent')) return null;
  if (attempt.status === status && attempt.refusal_reason === reason) return null;
  tx.run('UPDATE credential_request_delivery_attempts SET status=?,refusal_reason=? WHERE attempt_id=?', [status, reason, attempt_id]);
  const body = read(tx, attempt.request_id);
  if (body.delivery === null || body.delivery.attempt_id !== attempt_id) return null;
  body.delivery = { attempt_id, status, refusal_reason: reason };
  return update(tx, body, now);
}
function installed(sql: ReadSql): boolean {
  return sql.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name='credential_requests'") !== undefined;
}
function unixepoch(sql: ReadSql): number {
  return sql.get<{ now: number }>('SELECT unixepoch() AS now')!.now;
}
export function observe_events(tx: TransactionSql, conversation_id: string, payloads: readonly ConversationEventPayload[]): readonly ConversationEventPayload[] {
  if (!installed(tx)) return [];
  const events: ConversationEventPayload[] = [];
  for (const payload of payloads) {
    const status = delivery_status(payload);
    const identity = 'sender_message_id' in payload ? payload.sender_message_id : null;
    if (status === null || identity === null) continue;
    const attempt = tx.get<{ attempt_id: string }>(`
      SELECT a.attempt_id FROM credential_request_delivery_attempts a
      JOIN credential_requests r ON r.request_id=a.request_id
      WHERE a.sender_message_id=? AND r.conversation_id=?
    `, [identity, conversation_id]);
    if (attempt !== undefined) {
      const event = settle_attempt(tx, attempt.attempt_id, status[0], status[1], unixepoch(tx));
      if (event !== null) events.push(event);
    }
  }
  return events;
}
export function reconcile_deliveries(tx: TransactionSql): ReadonlyMap<string, readonly ConversationEventPayload[]> {
  if (!installed(tx)) return new Map();
  const grouped = new Map<string, ConversationEventPayload[]>();
  const attempts = tx.all<CredentialRequestDeliveryAttemptRow & { conversation_id: string }>(`
    SELECT a.*,r.conversation_id FROM credential_request_delivery_attempts a
    JOIN credential_requests r ON r.request_id=a.request_id
    WHERE a.status IN ('intent','queued')
  `);
  const now = unixepoch(tx);
  for (const attempt of attempts) {
    const receipt = tx.get<{ kind: ConversationEventKind; payload: string }>(`
      SELECT kind,payload FROM conversation_events WHERE conversation_id=?
      AND kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded')
      AND json_extract(payload,'$.sender_message_id')=? ORDER BY sequence DESC LIMIT 1
    `, [attempt.conversation_id, attempt.sender_message_id]);
    const status = receipt === undefined ? null : delivery_status(
      conversation_event_payload_from_canonical_json(receipt.kind, receipt.payload),
    );
    const event = settle_attempt(tx, attempt.attempt_id, status?.[0] ?? 'uncertain', status?.[1] ?? null, now);
    if (event !== null) {
      const events = grouped.get(attempt.conversation_id) ?? [];
      events.push(event);
      grouped.set(attempt.conversation_id, events);
    }
  }
  return grouped;
}
