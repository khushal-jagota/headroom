"""The complete list of Panels-owned inputs for a worker step."""

from __future__ import annotations

from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.ticket_progress import advance_ticket

from planner.runtime.logic.worker_step_prompt import compose_worker_step_prompt
from planner.tickets import data as tickets_data
from planner.tickets import revision_feedback
from planner.tickets.contracts import NO_FURTHER, Ticket, TicketEdit
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.mark.parametrize(
    ("stage", "field"),
    [
        ("needs_success_condition", "success_condition"),
        ("needs_what_changes", "what_changes"),
        ("needs_plan", "plan"),
        ("needs_implementation", "implementation"),
        ("needs_consequences", "consequences"),
    ],
)
def test_each_worker_stage_has_the_same_ordered_ticket_inputs(
    tmp_db: Connection,
    stage: str,
    field: str,
) -> None:
    ticket = _ticket_at_stage(tmp_db, stage)
    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(guidance="Keep the agreed boundary."),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    ticket = tickets_data.read_ticket(tmp_db, ticket.id)

    prompt = compose_worker_step_prompt(
        ticket,
        worker_type_definition=configured_worker_type_registry().require(ticket.worker_type),
        revision_feedback=None,
    )

    assert [item.name for item in prompt.inputs] == [
        "stage instruction",
        "Ticket guidance",
        "Ticket brief",
    ]
    assert prompt.inputs[0].text == (
        f"Work ticket {ticket.id} — Ticket inputs. It is at Stage '{stage}'; take the next step "
        f"and propose the '{field}' field for approval. Stage owner: worker."
    )
    assert prompt.model_text == (
        f"{prompt.inputs[0].text}\n\n"
        "[Ticket guidance]\nKeep the agreed boundary.\n[/Ticket guidance]\n\n"
        "[Ticket brief]\nStart with the settled brief.\n[/Ticket brief]"
    )


def test_revision_feedback_is_the_only_optional_worker_input_after_ticket_inputs(
    tmp_db: Connection,
) -> None:
    ticket = _ticket_at_stage(tmp_db, "needs_success_condition")
    feedback = revision_feedback.set_feedback(
        tmp_db,
        ticket.id,
        stage=ticket.stage,
        sender=OWNER_PRINCIPAL,
        message="Preserve this exact feedback.",
        now=4,
    )

    prompt = compose_worker_step_prompt(
        ticket,
        worker_type_definition=configured_worker_type_registry().require(ticket.worker_type),
        revision_feedback=feedback,
    )

    assert [item.name for item in prompt.inputs] == [
        "stage instruction",
        "Ticket brief",
        "Ticket revision feedback",
    ]
    assert prompt.inputs[-1].text == (
        "Revision feedback from owner owner for stage needs_success_condition:\n"
        "Preserve this exact feedback."
    )
    assert "[Pending worker context]" not in prompt.model_text


def _ticket_at_stage(tmp_db: Connection, stage: str) -> Ticket:
    ticket = tickets_data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="Ticket inputs",
        kickoff_note="Start with the settled brief.",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    ticket = tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=1,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    return advance_ticket(tmp_db, ticket.id, new_stage=stage, principal=OWNER_PRINCIPAL, now=2)
