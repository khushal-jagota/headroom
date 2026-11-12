import type { Principal } from '../core/contracts.ts';

export type WakeSourceKind = 'proposal' | 'worker_error' | 'turn_failure' | 'source_failure';
export type WakeBatchStatus =
  | 'pending'
  | 'offering'
  | 'dispatching'
  | 'accepted'
  | 'uncertain'
  | 'refused'
  | 'discarded'
  | 'delivered';
export interface WakeBatch {
  id: number;
  manager_id: string;
  target: Principal;
  sender_message_id: string;
  message: string;
  status: WakeBatchStatus;
  conversation_id: string | null;
  process_token: string | null;
}
export interface FailureNoticeIdentity {
  source_id: string;
  episode: number;
  responsibility_revision: number;
  recipient: Principal;
}
export interface ProposalNoticeState {
  ticket_exists: boolean;
  pending_revision: number | null;
  reviewer: Principal | null;
}
export type ProposalNoticeSuppressionReason =
  'ticket_missing' | 'proposal_resolved' | 'revision_changed' | 'reviewer_changed';
export interface ProposalNoticeSuppression {
  wake_id: number;
  ticket_id: string | null;
  source_revision: number;
  target: Principal;
  current: ProposalNoticeState;
  reason: ProposalNoticeSuppressionReason;
}
