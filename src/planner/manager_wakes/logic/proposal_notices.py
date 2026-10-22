"""Eligibility of an existing proposal notice, independent of new proposal admission."""

from planner.core.contracts import Principal
from planner.manager_wakes.contracts import ProposalNoticeState, ProposalNoticeSuppressionReason


def suppression_reason(
    *, source_revision: int, target: Principal, current: ProposalNoticeState
) -> ProposalNoticeSuppressionReason | None:
    if not current.ticket_exists:
        return ProposalNoticeSuppressionReason.ticket_missing
    if current.pending_revision is None:
        return ProposalNoticeSuppressionReason.proposal_resolved
    if source_revision != current.pending_revision:
        return ProposalNoticeSuppressionReason.revision_changed
    if target != current.reviewer:
        return ProposalNoticeSuppressionReason.reviewer_changed
    return None
