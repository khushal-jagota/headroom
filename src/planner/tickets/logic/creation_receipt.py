"""Explain the existing proposal routes without granting authority or changing them."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode, Ticket, TicketCreationReceipt
from planner.tickets.logic.proposal_routing import ProposalRouteKind, route_proposal
from planner.worker_types.contracts import BRIEF_FIELD_ID, WorkerTypeDefinition


def supervision_receipt(
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
    review_recipient: str,
) -> TicketCreationReceipt:
    definition = worker_type_definition
    stages = tuple(stage for stage in definition.stages if not stage.is_terminal)
    auto_accept = tuple(
        stage
        for stage in stages
        if route_proposal(
            stage=stage.id,
            ceiling=ticket.ceiling,
            holder=ticket.ceiling_holder,
            worker_type_definition=definition,
        ).kind
        is ProposalRouteKind.auto_accept
    )
    current_index = definition.stage_index(ticket.stage)
    remaining = tuple(
        stage for stage in stages if definition.stage_index(stage.id) >= current_index
    )
    autonomous = tuple(
        stage
        for stage in remaining
        if stage in auto_accept and stage.ownership_mode is StageOwnershipMode.worker
    )
    review = next((stage for stage in remaining if stage not in auto_accept), None)
    parts: list[str] = []
    if review is None:
        parts.append("Nothing in this grant ever parks for review.")
    if not auto_accept:
        parts.append("Granted scope: no Stage proposals auto-accept without review.")
    parts.append(
        "Runs unsupervised: " + ", ".join(stage.label for stage in autonomous) + "."
        if autonomous
        else "No remaining Worker Stage runs unsupervised."
    )
    if BRIEF_FIELD_ID in ticket.field_values:
        parts.append("Brief is settled at creation.")
    user_stages = tuple(
        stage
        for stage in remaining
        if stage in auto_accept and stage.ownership_mode is StageOwnershipMode.user
    )
    if user_stages:
        parts.append(
            "User involvement is required at "
            + ", ".join(stage.label for stage in user_stages)
            + ", even though its proposals auto-accept."
        )
    if review is not None:
        if auto_accept:
            parts.append(f"First parks for review at {review.label}, with {review_recipient}.")
        else:
            parts.append(
                f"The Ticket first parks for proposal review at {review.label}, "
                f"with {review_recipient}."
            )
        if review.ownership_mode is StageOwnershipMode.user:
            parts.append(f"{review.label} requires user involvement before its proposal review.")
    return TicketCreationReceipt(supervision_receipt=" ".join(parts))
