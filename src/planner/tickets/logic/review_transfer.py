"""Whether a parked review moved to another holder."""

from planner.core.contracts import Principal, PrincipalKind
from planner.tickets.contracts import Ticket


def review_transferred(before: Ticket, after: Ticket) -> bool:
    return (
        before.pending_proposal is not None
        and before.pending_proposal == after.pending_proposal
        and before.ceiling_holder != after.ceiling_holder
    )


def notify_new_review_holder(holder: Principal) -> bool:
    return holder.kind is not PrincipalKind.owner
