"""The ordered inputs Panels sends into a Ticket's conversation for one worker step.

A wake carries only what a Worker cannot get for itself. The Ticket brief and the Ticket
guidance are not that: they sit in the database, and one command returns them. What a
Worker cannot get is the knowledge that it lost its memory, so that is what was added
when those two were removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from planner.tickets.contracts import StageOwnershipMode, Ticket
from planner.tickets.logic import machine
from planner.tickets.revision_feedback import PendingRevisionFeedback
from planner.worker_types.contracts import WorkerTypeDefinition

READ_YOUR_TICKET_COMMAND: Final = "panels worker my-ticket brief,guidance"

MEMORY_LOSS_NOTICE: Final = (
    "Your context was compacted, so the conversation you were working from is gone. "
    f"Re-read your Ticket with {READ_YOUR_TICKET_COMMAND}, re-load your Worker skill, "
    "then continue this step."
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
    worker_type_definition: WorkerTypeDefinition,
    revision_feedback: PendingRevisionFeedback | None,
    memory_was_lost: bool,
) -> WorkerStepPrompt:
    """Compose every Panels-owned input for one Ticket worker step.

    Worker skills bind outside this value. Conversation history and authenticated reply
    requirements also remain in the conversation system, where their own trust rules
    apply. The gated field is resolved against the Ticket's own Worker type definition.

    ``memory_was_lost`` is the caller's answer to whether this conversation was compacted
    since Panels last spoke into it. Only Panels holds that fact, so only Panels can put
    it in the message.
    """
    gating = worker_type_definition.gating_field(ticket.stage)
    field = str(gating) if gating is not None else "the next step"

    ownership_mode = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    ownership_wire = ownership_mode.value if ownership_mode is not None else "terminal"
    if ownership_mode is StageOwnershipMode.user:
        step_instruction = (
            f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
            f"open the collaborative discussion for the '{field}' field. "
            "Ask bounded questions or resume the Stage conversation, and do not file a "
            "proposal until the discussion has enough shared understanding. "
            f"Stage owner: {ownership_wire}."
        )
    else:
        step_instruction = (
            f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
            f"take the next step and propose the '{field}' field for approval. "
            f"Stage owner: {ownership_wire}."
        )
    step_instruction = f"{step_instruction} Read your Ticket first: {READ_YOUR_TICKET_COMMAND}."

    inputs: list[WorkerStepInput] = []
    if memory_was_lost:
        inputs.append(WorkerStepInput("memory loss", MEMORY_LOSS_NOTICE, quoted=False))
    inputs.append(WorkerStepInput("stage instruction", step_instruction, quoted=False))
    if revision_feedback is not None:
        inputs.append(WorkerStepInput("Ticket revision feedback", revision_feedback.text))
    return WorkerStepPrompt(inputs=tuple(inputs))


def mid_step_memory_loss_prompt(ticket: Ticket) -> str:
    """Say the same thing to a Worker that lost its memory part-way through a step.

    No stage instruction follows this one, so it names the Ticket and Stage itself.
    """
    return (
        f"You are part-way through a step on ticket {ticket.id} — {ticket.title}, "
        f"at Stage '{str(ticket.stage)}'. {MEMORY_LOSS_NOTICE}"
    )


def _render_worker_step_input(item: WorkerStepInput) -> str:
    if not item.quoted:
        return item.text
    return f"[{item.name}]\n{item.text}\n[/{item.name}]"
