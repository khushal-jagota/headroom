import { createHash } from 'node:crypto';
import type { NavigationRecord, ViewRecord } from '../../../web/src/lib/navigation/contracts.ts';
import type { Fact, Key } from './contracts.ts';
import type { TicketNeed } from '../../tickets/contracts.ts';
import { compact } from '../logic/json.ts';
import { VIEW_CONFIG } from './configuration.ts';
export const GROUPS = [
  'awaiting_approval',
  'awaiting_answer',
  'assigned',
  'awaiting_reply',
  'errored',
  'backlog',
  'agent',
  'status_awaiting_approval',
  'empty',
  'blocked',
  'done',
] as const;
export function sectionKey(
  section: string,
  owner: Key | null,
  group = 'all',
  childFilter = 'all',
): string {
  return compact([section, owner, group, childFilter]);
}
/**
 * A record as the view sends it. `withPendingAsk` is false outside the review window: only a
 * Review card answers the waiting ask, so other windows do not spend their budget on it. The
 * change digest keeps it, so a changed ask still reaches the review window.
 */
export function project(
  record: NavigationRecord,
  settledOnly = false,
  withPendingAsk = true,
): ViewRecord {
  const value: Fact = { ...record.value };
  const { kind, id } = record;
  if (settledOnly && kind === 'manager' && value.settled) {
    return {
      kind,
      id,
      value: Object.fromEntries(
        ['title', 'created_at', 'updated_at', 'settled', 'conversation_id'].map((name) => [
          name,
          value[name],
        ]),
      ),
    } as ViewRecord;
  }
  if (kind === 'conversation') {
    return {
      kind,
      id,
      value: Object.fromEntries(
        [
          'owner_read_through_sequence',
          'first_unread_owner_message_at',
          'latest_ask_at',
          'latest_owner_message_sequence',
          ...(withPendingAsk ? ['pending_ask'] : []),
          'error_acknowledged_through_sequence',
          'attention',
        ].map((name) => [name, value[name]]),
      ),
    } as ViewRecord;
  }
  if (kind === 'ticket' || kind === 'manager') {
    const recap = String(value.recap),
      size = Buffer.byteLength(recap);
    value.recap_reference = null;
    if (size > VIEW_CONFIG.inline_recap_bytes) {
      value.recap = value.recap_preview;
      value.recap_reference = {
        owner: { kind, id },
        version: createHash('sha256').update(recap).digest('hex'),
        utf8_bytes: size,
      };
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
  // A Worker that is setting up is active, like a working one.
  const working = !!attention?.signal.working || !!value.worker_setup,
    failed = !!attention?.last_turn_failed;
  if (!working && (value.ticket_status === 'errored' || (failed && value.stage !== 'done')))
    return 'errored';
  if (value.ticket_status === 'agent' || working) return 'agent';
  if (parked && !owner) return 'status_awaiting_approval';
  if (value.stage === 'done') return 'done';
  return 'empty';
}
const NEED_BY_GROUP: Record<(typeof GROUPS)[number], TicketNeed> = {
  awaiting_approval: 'owner_approval',
  awaiting_answer: 'owner_answer',
  assigned: 'owner_stage',
  awaiting_reply: 'owner_reply',
  status_awaiting_approval: 'agent_approval',
  agent: 'working',
  empty: 'ready',
  blocked: 'blocked',
  errored: 'errored',
  backlog: 'backlog',
  done: 'done',
};
/** A Ticket is terminal at its workflow's last Stage. Old records only say `done`. */
export function ticketIsTerminal(value: Fact): boolean {
  return !!value.terminal || value.stage === 'done';
}
/**
 * The one word for what a Ticket needs next. Backlog and done win over every waiting word, and
 * they never read `attention`, so a caller may pass null for those Tickets.
 */
export function ticket_need(value: Fact, attention: Fact | null): TicketNeed {
  if ('integrity_error' in value) return 'errored';
  if (value.is_backlogged) return 'backlog';
  if (ticketIsTerminal(value)) return 'done';
  return NEED_BY_GROUP[group(value, attention, false) as (typeof GROUPS)[number]];
}
export function eligibleForOwnerReview(value: Fact): boolean {
  return (
    value.ticket_status === 'awaiting_approval' &&
    !value.is_backlogged &&
    compact(value.ceiling_holder) === compact({ kind: 'owner', id: 'owner' }) &&
    value.gating_field != null &&
    value.proposal_field != null &&
    value.proposal_created_at != null
  );
}
export function messageOwnerIsLive(kind: string, owner: Fact): boolean {
  if (kind === 'ticket') return owner.stage !== 'done' && !owner.is_backlogged;
  return kind !== 'manager' || !owner.settled;
}
