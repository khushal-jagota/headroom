"""The replaceable decision seam between a proposal and its destination."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.tickets.contracts import StageOwnershipMode
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition


class ProposalRouteKind(StrEnum):
    auto_accept = "auto_accept"
    item_manager = "item_manager"
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
    ownership = machine.stage_ownership_mode(stage, worker_type_definition=worker_type_definition)
    below_the_ceiling = not machine.at_or_beyond_ceiling(
        stage, ceiling, worker_type_definition=worker_type_definition
    )
    if ownership is StageOwnershipMode.worker and below_the_ceiling:
        return ProposalRoute(ProposalRouteKind.auto_accept)
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
    return OWNER_PRINCIPAL
