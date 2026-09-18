"""The text Panels itself sends into a Ticket's conversation.

Two lifecycle prompts live here. One asks the worker to take the Ticket's next step; the
other records its proposal rejection before the decider's separately attributed comment
arrives. Nothing here reads a database, a clock or a conversation.
"""

from __future__ import annotations

from typing import Final

from planner.tickets.contracts import StageOwnershipMode, Ticket
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition

PROPOSAL_RETURNED_FOR_REVISION: Final = (
    "Your proposal was rejected and returned for revision. The decider's comment follows."
)


def worker_step_prompt(
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> str:
    """Describe what to advance; the worker role skill owns how to do the work.

    The Worker type selects the specialist skill; this prompt carries only the current
    Stage ownership, the gated field to advance, and current Ticket guidance.
    The gating field is resolved against the ticket's OWN type definition (not the
    coding default), so a novel-stage type (e.g. new_worker at needs_understanding) reads
    its real field instead of raising 'stage outside the linear order'.
    """
    gating = worker_type_definition.gating_field(ticket.stage)
    field = str(gating) if gating is not None else "the next step"

    ownership_mode = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    ownership_wire = ownership_mode.value if ownership_mode is not None else "terminal"
    guidance = (
        f"\n\n[Ticket guidance]\n{ticket.guidance}\n[/Ticket guidance]" if ticket.guidance else ""
    )
    if ownership_mode is StageOwnershipMode.user:
        return (
            f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
            f"open the collaborative discussion for the '{field}' field. "
            "Ask bounded questions or resume the Stage conversation, and do not file a "
            "proposal until the discussion has enough shared understanding. "
            f"Stage owner: {ownership_wire}.{guidance}"
        )
    return (
        f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
        f"take the next step and propose the '{field}' field for approval. "
        f"Stage owner: {ownership_wire}.{guidance}"
    )


def proposal_returned_for_revision_prompt() -> str:
    """Tell the worker the lifecycle transition separately from anyone's comment."""
    return PROPOSAL_RETURNED_FOR_REVISION
