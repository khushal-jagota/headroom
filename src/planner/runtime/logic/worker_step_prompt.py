"""The text Panels itself sends into a Ticket's conversation.

Two openers live here. One asks the worker to take the Ticket's next step; the other
carries the owner's guidance after a proposal was sent back. Nothing here reads a
database, a clock or a conversation — a Ticket and its Worker type go in, text comes out.
"""

from __future__ import annotations

from typing import Final

from planner.tickets.contracts import StageOwnershipMode, Ticket
from planner.worker_types.contracts import WorkerTypeDefinition

REVISION_GUIDANCE_PREFIX: Final = (
    "The user rejected your proposal and provided the following guidance:"
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

    ownership_wire = (
        ticket.effective_stage_ownership_mode.value
        if ticket.effective_stage_ownership_mode is not None
        else "terminal"
    )
    guidance = (
        f"\n\n[Ticket guidance]\n{ticket.guidance}\n[/Ticket guidance]" if ticket.guidance else ""
    )
    if ticket.effective_stage_ownership_mode is StageOwnershipMode.paired:
        return (
            f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
            f"open the paired discussion for the '{field}' field. "
            "Ask bounded questions or resume the Stage conversation, and do not file a "
            "proposal until the discussion has enough shared understanding. "
            f"Stage owner: {ownership_wire}.{guidance}"
        )
    return (
        f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
        f"take the next step and propose the '{field}' field for approval. "
        f"Stage owner: {ownership_wire}.{guidance}"
    )


def revision_guidance_prompt(guidance: str) -> str:
    """Carry the owner's guidance back to the worker whose proposal was returned."""
    return f"{REVISION_GUIDANCE_PREFIX}\n\n{guidance}"
