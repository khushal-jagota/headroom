"""The ordered inputs Panels sends into a Ticket's conversation for one worker step."""

from __future__ import annotations

from dataclasses import dataclass

from planner.tickets.contracts import StageOwnershipMode, Ticket
from planner.tickets.logic import machine
from planner.tickets.revision_feedback import PendingRevisionFeedback
from planner.worker_types.contracts import BRIEF_FIELD_ID, WorkerTypeDefinition


@dataclass(frozen=True, slots=True)
class WorkerStepInput:
    """One named source of text for a Ticket worker step."""

    name: str
    text: str


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

    inputs = [WorkerStepInput("stage instruction", step_instruction)]
    if ticket.guidance:
        inputs.append(WorkerStepInput("Ticket guidance", ticket.guidance))
    brief = ticket.field_values.get(BRIEF_FIELD_ID)
    if brief:
        inputs.append(WorkerStepInput("Ticket brief", brief))
    if revision_feedback is not None:
        inputs.append(WorkerStepInput("Ticket revision feedback", revision_feedback.text))
    return WorkerStepPrompt(inputs=tuple(inputs))


def _render_worker_step_input(item: WorkerStepInput) -> str:
    if item.name == "stage instruction":
        return item.text
    return f"[{item.name}]\n{item.text}\n[/{item.name}]"
