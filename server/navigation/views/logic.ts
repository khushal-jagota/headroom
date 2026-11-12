import { createHash } from 'node:crypto';
import type { NavigationRecord, ViewRecord } from '../../../web/src/lib/navigation/contracts.ts';
import type { Fact, Key } from './contracts.ts';
import { compact } from '../logic/json.ts';
import { VIEW_CONFIG } from './configuration.ts';
export const GROUPS = [
  'awaiting_approval', 'awaiting_answer', 'assigned', 'awaiting_reply', 'errored',
  'backlog', 'agent', 'waiting_to_closeout', 'status_awaiting_approval', 'empty', 'blocked', 'done',
] as const;
export function sectionKey(section: string, owner: Key | null, group = 'all', childFilter = 'all'): string {
  return compact([section, owner, group, childFilter]);
}
export function project(record: NavigationRecord, settledOnly = false): ViewRecord {
  const value: Fact = { ...record.value };
  const { kind, id } = record;
  if (settledOnly && kind === 'manager' && value.settled) {
    return { kind, id, value: Object.fromEntries(['title', 'created_at', 'updated_at', 'settled', 'conversation_id'].map(name => [name, value[name]])) } as ViewRecord;
  }
  if (kind === 'conversation') {
    return { kind, id, value: Object.fromEntries(['owner_read_through_sequence', 'first_unread_owner_message_at', 'error_acknowledged_through_sequence', 'attention'].map(name => [name, value[name]])) } as ViewRecord;
  }
  if (kind === 'ticket' || kind === 'manager') {
    const recap = String(value.recap), size = Buffer.byteLength(recap);
    value.recap_reference = null;
    if (size > VIEW_CONFIG.inline_recap_bytes) {
      value.recap = value.recap_preview;
      value.recap_reference = { owner: { kind, id }, version: createHash('sha256').update(recap).digest('hex'), utf8_bytes: size };
    }
  }
  return { kind, id, value } as ViewRecord;
}
export function group(value: Fact, attention: Fact | null, rail: boolean): string {
  if ('integrity_error' in value) return 'errored';
  if (value.is_backlogged) return 'backlog';
  const owner = compact(value.ceiling_holder) === compact({ kind: 'owner', id: 'owner' });
  const parked = value.ticket_status === 'awaiting_approval';
  if (parked && owner) return 'awaiting_approval';
  if (attention?.awaiting_answer) return 'awaiting_answer';
  if (value.stage_ownership === 'user') return 'assigned';
  if (!rail && attention?.signal.message_for_owner) return 'awaiting_reply';
  if (value.blocked || value.ticket_status === 'blocked') return 'blocked';
  const working = !!attention?.signal.working, failed = !!attention?.last_turn_failed;
  if (!working && (value.ticket_status === 'errored' || failed && value.stage !== 'done')) return 'errored';
  if (value.ticket_status === 'agent' || working) return 'agent';
  if (value.gating_field === 'consequences' && value.ticket_status === 'empty') return 'waiting_to_closeout';
  if (parked && !owner) return 'status_awaiting_approval';
  if (value.stage === 'done') return 'done';
  return 'empty';
}
export function eligibleForOwnerReview(value: Fact): boolean {
  return value.ticket_status === 'awaiting_approval' && !value.is_backlogged
    && compact(value.ceiling_holder) === compact({ kind: 'owner', id: 'owner' })
    && value.gating_field != null && value.proposal_field != null && value.proposal_created_at != null;
}
export function messageOwnerIsLive(kind: string, owner: Fact): boolean {
  if (kind === 'ticket') return owner.stage !== 'done' && !owner.is_backlogged;
  return kind !== 'manager' || !owner.settled;
}
