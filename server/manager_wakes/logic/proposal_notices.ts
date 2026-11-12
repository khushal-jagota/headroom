import type { Principal } from '../../core/contracts.ts';
import type { ProposalNoticeState, ProposalNoticeSuppressionReason } from '../contracts.ts';

export function suppression_reason(options: {
  source_revision: number;
  target: Principal;
  current: ProposalNoticeState;
}): ProposalNoticeSuppressionReason | null {
  const { current, source_revision, target } = options;
  if (!current.ticket_exists) return 'ticket_missing';
  if (current.pending_revision === null) return 'proposal_resolved';
  if (source_revision !== current.pending_revision) return 'revision_changed';
  if (target.kind !== current.reviewer?.kind || target.id !== current.reviewer?.id)
    return 'reviewer_changed';
  return null;
}
