from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL, TEST_TICKET_PRINCIPAL, ticket_principal

from planner.core.contracts import Priority
from planner.core.db import connect
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    Ticket,
    TicketEdit,
)
from planner.tickets.worker_context import TICKET_CHANGED_CONTEXT_KEY, TICKET_CHANGED_TEXT
from planner.worker_context import data, revision_feedback
from planner.worker_context.contracts import PendingWorkerContext, WorkerContextReceipt
from planner.worker_context.service import SqliteWorkerContextService


def test_pending_context_coalesces_by_worker_and_key_with_monotonic_revisions(
    tmp_db: Connection,
) -> None:
    first = data.set_context(tmp_db, "t_one", "ticket_changed", "first")
    second = data.set_context(tmp_db, "t_one", "ticket_changed", "second")
    other_key = data.set_context(tmp_db, "t_one", "other", "another")
    data.set_context(tmp_db, "t_two", "ticket_changed", "separate")

    assert first.revision == 1
    assert second.revision == 2
    assert other_key.revision == 1
    assert [
        (item.context_key, item.text, item.revision)
        for item in data.snapshot(tmp_db, "t_one").items
    ] == [
        ("other", "another", 1),
        ("ticket_changed", "second", 2),
    ]
    assert [item.text for item in data.snapshot(tmp_db, "t_two").items] == ["separate"]


def test_acknowledge_removes_only_exact_key_revision_pairs(tmp_db: Connection) -> None:
    stale = data.set_context(tmp_db, "t_one", "ticket_changed", "first")
    other = data.set_context(tmp_db, "t_one", "other", "another")
    current = data.set_context(tmp_db, "t_one", "ticket_changed", "updated during send")

    data.acknowledge(
        tmp_db,
        "t_one",
        (
            WorkerContextReceipt(stale.context_key, stale.revision),
            WorkerContextReceipt(other.context_key, other.revision),
        ),
    )

    remaining = data.snapshot(tmp_db, "t_one").items
    assert [(item.context_key, item.text, item.revision) for item in remaining] == [
        ("ticket_changed", "updated during send", current.revision)
    ]


def test_sqlite_service_composes_prompt_and_acknowledges_only_prepared_revision(
    tmp_db: Connection,
) -> None:
    db_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()["file"]))
    service = SqliteWorkerContextService(lambda: connect(str(db_path)))
    first = data.set_context(tmp_db, "t_one", "ticket_changed", "Reread the ticket.")

    prepared = service.prepare("t_one", "Continue working.")
    assert prepared.model_text == (
        "Continue working.\n\n[Pending worker context]\n- Reread the ticket."
        "\n[/Pending worker context]"
    )
    assert prepared.receipts == (WorkerContextReceipt("ticket_changed", first.revision),)

    data.set_context(tmp_db, "t_one", "ticket_changed", "Reread the newer ticket.")
    service.acknowledge("t_one", prepared.receipts)

    assert [(item.text, item.revision) for item in data.snapshot(tmp_db, "t_one").items] == [
        ("Reread the newer ticket.", 2)
    ]


def test_revision_feedback_is_attributed_stage_scoped_bounded_and_one_use(
    tmp_db: Connection,
) -> None:
    ticket = _ticket(tmp_db)
    db_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()["file"]))
    service = SqliteWorkerContextService(lambda: connect(str(db_path)))
    first = revision_feedback.set_feedback(
        tmp_db,
        ticket.id,
        stage=ticket.stage,
        sender=OWNER_PRINCIPAL,
        message="first feedback",
        now=2,
    )
    second = revision_feedback.set_feedback(
        tmp_db,
        ticket.id,
        stage=ticket.stage,
        sender=TEST_TICKET_PRINCIPAL,
        message="  exact replacement\nwith spacing  ",
        now=3,
    )
    assert second.revision == first.revision + 1
    assert (
        tmp_db.execute(
            "SELECT count(*) FROM ticket_revision_feedback WHERE ticket_id=?", (ticket.id,)
        ).fetchone()[0]
        == 1
    )

    prepared = service.prepare(ticket.id, "Continue working.")
    assert "ticket t_test" in prepared.model_text
    assert "first feedback" not in prepared.model_text
    assert "  exact replacement\nwith spacing  " in prepared.model_text
    service.acknowledge(ticket.id, prepared.receipts)

    assert revision_feedback.snapshot(tmp_db, ticket.id) is None
    assert "Revision feedback" not in service.prepare(ticket.id, "Next prompt.").model_text


def test_revision_feedback_for_an_old_stage_does_not_enter_the_prompt(
    tmp_db: Connection,
) -> None:
    ticket = _ticket(tmp_db)
    db_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()["file"]))
    service = SqliteWorkerContextService(lambda: connect(str(db_path)))
    revision_feedback.set_feedback(
        tmp_db,
        ticket.id,
        stage="needs_plan",
        sender=OWNER_PRINCIPAL,
        message="Do not leak this into success.",
        now=2,
    )

    prepared = service.prepare(ticket.id, "Work success.")

    assert "Do not leak" not in prepared.model_text
    assert prepared.receipts == ()


def _ticket(tmp_db: Connection) -> Ticket:
    ticket = tickets_data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="Context ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    return tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        principal=OWNER_PRINCIPAL,
        now=1,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )


def _pending(tmp_db: Connection, ticket_id: str) -> tuple[PendingWorkerContext, ...]:
    return data.snapshot(tmp_db, ticket_id).items


def _clear(tmp_db: Connection, ticket_id: str) -> None:
    snapshot = data.snapshot(tmp_db, ticket_id)
    data.acknowledge(tmp_db, ticket_id, snapshot.receipts)


def test_human_ticket_edits_coalesce_but_agent_writes_do_not_produce_context(
    tmp_db: Connection,
) -> None:
    ticket = _ticket(tmp_db)

    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(priority=Priority.P1),
        title_max_chars=TITLE_MAX_CHARS,
        principal=TEST_TICKET_PRINCIPAL,
        now=2,
    )
    tickets_data.replace_guidance(
        tmp_db,
        ticket.id,
        body="agent note",
        principal=TEST_TICKET_PRINCIPAL,
        now=3,
    )
    assert _pending(tmp_db, ticket.id) == ()
    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(title="Human title"),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=4,
    )
    tickets_data.edit_field_value(
        tmp_db,
        ticket.id,
        field="kickoff",
        new_body="ticket guidance",
        principal=OWNER_PRINCIPAL,
        now=5,
    )
    tickets_data.replace_guidance(
        tmp_db,
        ticket.id,
        body="field guidance",
        principal=OWNER_PRINCIPAL,
        now=6,
    )

    pending = [(item.context_key, item.text, item.revision) for item in _pending(tmp_db, ticket.id)]
    assert pending == [(TICKET_CHANGED_CONTEXT_KEY, TICKET_CHANGED_TEXT, 3)]


@pytest.mark.parametrize(
    ("field", "prior_fields"),
    [
        ("success", ()),
        ("approach", ("success",)),
        ("plan", ("success", "approach")),
    ],
)
def test_only_edited_approval_produces_context_at_each_approval_gate(
    tmp_db: Connection, field: str, prior_fields: tuple[str, ...]
) -> None:
    def parked_ticket(edited: bool) -> str:
        ticket = _ticket(tmp_db)
        ceiling = {
            "success": "needs_success",
            "approach": "needs_approach",
            "plan": "needs_plan",
        }[field]
        tickets_data.set_ceiling(
            tmp_db,
            ticket.id,
            ceiling=ceiling,
            principal=OWNER_PRINCIPAL,
            now=10,
        )
        for prior in prior_fields:
            tickets_data.file_current_proposal_with_recap(
                tmp_db,
                ticket.id,
                body=prior,
                principal=ticket_principal(ticket.id),
                now=11,
                recap="Current work",
            )
        tickets_data.file_current_proposal_with_recap(
            tmp_db,
            ticket.id,
            body="draft",
            principal=ticket_principal(ticket.id),
            now=12,
            recap="Current work",
        )
        _clear(tmp_db, ticket.id)
        tickets_data.accept_proposal(
            tmp_db,
            ticket.id,
            field=field,
            principal=OWNER_PRINCIPAL,
            now=13,
            edited_body="edited" if edited else None,
            next_ceiling=NO_FURTHER,
            next_holder=OWNER_PRINCIPAL,
        )
        return ticket.id

    unedited_id = parked_ticket(False)
    edited_id = parked_ticket(True)

    assert _pending(tmp_db, unedited_id) == ()
    assert [(item.context_key, item.text) for item in _pending(tmp_db, edited_id)] == [
        (TICKET_CHANGED_CONTEXT_KEY, TICKET_CHANGED_TEXT)
    ]


def test_direct_value_and_scope_edits_produce_context_but_plain_accept_does_not(
    tmp_db: Connection,
) -> None:
    ticket = _ticket(tmp_db)
    tickets_data.set_ceiling(
        tmp_db,
        ticket.id,
        ceiling="needs_closeout",
        principal=OWNER_PRINCIPAL,
        now=20,
    )
    for field in ("success", "approach", "plan", "implementation"):
        tickets_data.file_current_proposal_with_recap(
            tmp_db,
            ticket.id,
            body=field,
            principal=ticket_principal(ticket.id),
            now=21,
            recap="Current work",
        )
    _clear(tmp_db, ticket.id)

    tickets_data.edit_field_value(
        tmp_db,
        ticket.id,
        field="success",
        new_body="edited success",
        principal=OWNER_PRINCIPAL,
        now=22,
    )
    assert len(_pending(tmp_db, ticket.id)) == 1
    _clear(tmp_db, ticket.id)

    tickets_data.file_current_proposal_with_recap(
        tmp_db,
        ticket.id,
        body="closeout draft",
        principal=ticket_principal(ticket.id),
        now=23,
        recap="Current work",
    )
    _clear(tmp_db, ticket.id)
    tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="closeout",
        principal=OWNER_PRINCIPAL,
        now=24,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    assert _pending(tmp_db, ticket.id) == ()


def test_human_recap_marks_context_but_agent_recap_does_not(tmp_db: Connection) -> None:
    ticket = _ticket(tmp_db)
    tickets_data.file_current_proposal_with_recap(
        tmp_db,
        ticket.id,
        body="success",
        principal=ticket_principal(ticket.id),
        now=28,
        recap="Current work",
    )
    tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=29,
        edited_body=None,
        next_ceiling="needs_plan",
        next_holder=OWNER_PRINCIPAL,
    )

    tickets_data.write_recap(
        tmp_db, ticket.id, body="agent recap", principal=TEST_TICKET_PRINCIPAL, now=30
    )
    assert _pending(tmp_db, ticket.id) == ()

    tickets_data.write_recap(
        tmp_db, ticket.id, body="human recap", principal=OWNER_PRINCIPAL, now=31
    )
    assert [(item.context_key, item.revision) for item in _pending(tmp_db, ticket.id)] == [
        (TICKET_CHANGED_CONTEXT_KEY, 1)
    ]


def test_deleting_ticket_removes_its_pending_context(tmp_db: Connection) -> None:
    ticket = _ticket(tmp_db)
    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(title="Changed before delete"),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=40,
    )
    assert _pending(tmp_db, ticket.id)

    tickets_data.delete_ticket(tmp_db, ticket.id, principal=OWNER_PRINCIPAL, now=41)

    assert _pending(tmp_db, ticket.id) == ()
