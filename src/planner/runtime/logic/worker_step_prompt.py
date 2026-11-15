"""The ordered inputs Panels sends into a Ticket's conversation for one worker step.

A wake carries only what a Worker cannot get for itself. The Ticket brief sits in the
database, and one command returns it. What a Worker cannot get is the knowledge that it
lost its memory, so Panels includes that fact when the conversation was compacted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from planner.tickets.contracts import StageOwnershipMode, Ticket
from planner.tickets.logic import machine
from planner.worker_types.contracts import CONSEQUENCES_FIELD_ID, WorkflowDefinition
from planner.worker_types.guidance import render_workflow_guide

READ_YOUR_TICKET_COMMAND: Final = "panels worker my-ticket brief"
# A Ticket copies its workflow when it is made, and a later edit to its Worker type does
# not reach it. So every Worker follows the Ticket's own guide, never the type's skill.
FOLLOW_YOUR_WORKFLOW_GUIDE: Final = (
    "Follow this Ticket’s workflow guide. "
    "Read the full guide again with panels worker my-ticket workflow if needed."
)

MEMORY_LOSS_NOTICE: Final = (
    "Your context was compacted, so the conversation you were working from is gone. "
    f"Re-read your Ticket with {READ_YOUR_TICKET_COMMAND}, re-load the panels-worker skill, "
    "follow this Ticket’s workflow guide, then continue this step."
)


@dataclass(frozen=True, slots=True)
class WorkerStepInput:
    """One named source of text for a Ticket worker step.

    ``quoted`` marks text that came from somewhere else and is shown inside its own
    named block. Panels' own sentences are not quoted: they are the message speaking.
    """

    name: str
    text: str
    quoted: bool = True


@dataclass(frozen=True, slots=True)
class WorkerStepPrompt:
    """The complete, inspectable list of inputs for a Ticket worker step."""

    inputs: tuple[WorkerStepInput, ...]

    @property
    def model_text(self) -> str:
        return "\n\n".join(_render_worker_step_input(item) for item in self.inputs)


def compose_worker_step_prompt(
    ticket: Ticket,
    *,
    worker_type_definition: WorkflowDefinition,
) -> WorkerStepPrompt:
    """Compose every Panels-owned input for one Ticket worker step.

    Worker skills bind outside this value. Conversation history and authenticated reply
    requirements also remain in the conversation system, where their own trust rules
    apply. The gated field is resolved against the Ticket's own Worker type definition.

    """
    gating = worker_type_definition.gating_field(ticket.stage)
    field = str(gating) if gating is not None else "the next step"

    ownership_mode = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    ownership_wire = ownership_mode.value if ownership_mode is not None else "terminal"
    if gating == CONSEQUENCES_FIELD_ID and ownership_mode is not StageOwnershipMode.user:
        step_instruction = (
            f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
            "complete the authorized Consequences work and submit its verified report. "
            "Consequences has no further approval gate. Its report settles automatically. "
            "Preserve actual blockers and required human work. "
            "Do not repeat completed side effects. "
            f"Stage owner: {ownership_wire}."
        )
    elif ownership_mode is StageOwnershipMode.user:
        step_instruction = (
            f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
            f"open the collaborative discussion for the '{field}' field. "
            "Ask bounded questions or resume the Stage conversation, and do not file a "
            "proposal until the discussion has enough shared understanding. "
            + (
                "Once the required human work is complete, the Consequences report settles "
                "automatically without another approval. "
                if gating == CONSEQUENCES_FIELD_ID
                else "Once filed, the answer settles below the ceiling and waits for approval "
                "at or beyond it. "
            )
            + f"Stage owner: {ownership_wire}."
        )
    else:
        step_instruction = (
            f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
            f"take the next step and propose the '{field}' field for approval. "
            f"Stage owner: {ownership_wire}."
        )
    step_instruction = (
        f"{step_instruction} Read your Ticket first: {READ_YOUR_TICKET_COMMAND}. "
        f"{FOLLOW_YOUR_WORKFLOW_GUIDE}"
    )

    inputs = [WorkerStepInput("stage instruction", step_instruction, quoted=False)]
    if gating == CONSEQUENCES_FIELD_ID:
        # A saved workflow may predate this completion rule. Send its actual work
        # and any recovered parked report into existing conversations as well.
        inputs.append(
            WorkerStepInput(
                "Consequences work and recovery",
                worker_type_definition.stage_definition(ticket.stage).instructions,
            )
        )
    return WorkerStepPrompt(inputs=tuple(inputs))


def worker_step_memory_loss_prompt(ticket: Ticket) -> str:
    """Panels' separate dispatch-time input for a worker-step wake."""
    return (
        MEMORY_LOSS_NOTICE
        + "\n\n[Ticket workflow guidance]\n"
        + render_workflow_guide(ticket.workflow)
        + "\n[/Ticket workflow guidance]"
    )


def mid_step_memory_loss_prompt(ticket: Ticket) -> str:
    """Say the same thing to a Worker that lost its memory part-way through a step.

    No stage instruction follows this one, so it names the Ticket and Stage itself.
    """
    guidance = (
        "\n\n[Ticket workflow guidance]\n"
        + render_workflow_guide(ticket.workflow)
        + "\n[/Ticket workflow guidance]"
    )
    return (
        f"You are part-way through a step on ticket {ticket.id} — {ticket.title}, "
        f"at Stage '{str(ticket.stage)}'. {MEMORY_LOSS_NOTICE}{guidance}"
    )


def _render_worker_step_input(item: WorkerStepInput) -> str:
    if not item.quoted:
        return item.text
    return f"[{item.name}]\n{item.text}\n[/{item.name}]"
