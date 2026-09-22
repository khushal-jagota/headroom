"""The replaceable decision seam between a proposal and its destination."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from planner.core.contracts import Principal, PrincipalKind
from planner.tickets.contracts import PendingTicketProposal
from planner.tickets.logic.decisions import Decision


class ProposalRouteKind(StrEnum):
    auto_accept = "auto_accept"
    item_manager = "item_manager"
    owner = "owner"


@dataclass(frozen=True, slots=True)
class ProposalRoute:
    kind: ProposalRouteKind
    sprint_item_id: str | None = None


def route_pending_proposal(
    pending_proposal: PendingTicketProposal | None, holder: Principal
) -> ProposalRoute:
    """Route deterministically. A later classifier can replace this function alone."""
    if pending_proposal is None:
        return ProposalRoute(ProposalRouteKind.auto_accept)
    if holder.kind is PrincipalKind.sprint_item:
        return ProposalRoute(ProposalRouteKind.item_manager, holder.id)
    return ProposalRoute(ProposalRouteKind.owner)


def route_proposal(decision: Decision) -> ProposalRoute:
    return route_pending_proposal(decision.pending_proposal, decision.ceiling_holder)
