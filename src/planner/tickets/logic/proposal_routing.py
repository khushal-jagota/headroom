"""The replaceable decision seam between a proposal and its destination."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition


class ProposalRouteKind(StrEnum):
    auto_accept = "auto_accept"
    item_manager = "item_manager"
    chief = "chief"
    owner = "owner"


@dataclass(frozen=True, slots=True)
class ProposalRoute:
    kind: ProposalRouteKind
    sprint_item_id: str | None = None


def route_proposal(
    *,
    stage: str,
    ceiling: str,
    holder: Principal,
    worker_type_definition: WorkerTypeDefinition,
) -> ProposalRoute:
    """Choose the proposal's action before its destination is persisted."""
    below_the_ceiling = not machine.at_or_beyond_ceiling(
        stage, ceiling, worker_type_definition=worker_type_definition
    )
    if below_the_ceiling:
        return ProposalRoute(ProposalRouteKind.auto_accept)
    return route_to_holder(holder)


def route_to_holder(holder: Principal) -> ProposalRoute:
    """Address a proposal that needs review, independently of new-filing admission."""
    if holder.kind is PrincipalKind.chief:
        return ProposalRoute(ProposalRouteKind.chief)
    if holder.kind is PrincipalKind.sprint_item:
        return ProposalRoute(ProposalRouteKind.item_manager, holder.id)
    return ProposalRoute(ProposalRouteKind.owner)


def parked_destination(route: ProposalRoute) -> Principal | None:
    """Translate the selected route into settlement or a persisted holder."""
    if route.kind is ProposalRouteKind.auto_accept:
        return None
    if route.kind is ProposalRouteKind.item_manager:
        assert route.sprint_item_id is not None
        return Principal(PrincipalKind.sprint_item, route.sprint_item_id)
    if route.kind is ProposalRouteKind.chief:
        return CHIEF_PRINCIPAL
    return OWNER_PRINCIPAL
