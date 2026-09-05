from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.contracts import Priority
from planner.core.db import connect
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    Ticket,
    TicketEdit,
)
from planner.tickets.worker_context import TICKET_CHANGED_CONTEXT_KEY, TICKET_CHANGED_TEXT
from planner.worker_context import data
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


def _ticket(tmp_db: Connection) -> Ticket:
    ticket = tickets_data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="Context ticket",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    return tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        actor="human",
        now=1,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
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
        actor="agent",
        now=2,
    )
    tickets_data.replace_guidance(
        tmp_db,
        ticket.id,
        body="agent note",
        actor="agent",
        now=3,
    )
    assert _pending(tmp_db, ticket.id) == ()
    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(title="Human title"),
        title_max_chars=TITLE_MAX_CHARS,
        actor="human",
        now=4,
    )
    tickets_data.edit_field_value(
        tmp_db,
        ticket.id,
        field="kickoff",
        new_body="ticket guidance",
        actor="human",
        now=5,
    )
    tickets_data.replace_guidance(
        tmp_db,
        ticket.id,
        body="field guidance",
        actor="human",
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
        tickets_data.change_scope(
            tmp_db,
            ticket.id,
            ceiling=ceiling,
            at_cap=AtCap.propose,
            actor="human",
            now=10,
        )
        for prior in prior_fields:
            tickets_data.file_proposal(
                tmp_db, ticket.id, field=prior, body=prior, actor="agent", now=11
            )
        tickets_data.file_proposal(
            tmp_db, ticket.id, field=field, body="draft", actor="agent", now=12
        )
        _clear(tmp_db, ticket.id)
        tickets_data.accept_proposal(
            tmp_db,
            ticket.id,
            field=field,
            actor="human",
            now=13,
            edited_body="edited" if edited else None,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
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
    tickets_data.change_scope(
        tmp_db,
        ticket.id,
        ceiling="needs_closeout",
        at_cap=AtCap.propose,
        actor="human",
        now=20,
    )
    for field in ("success", "approach", "plan", "implementation"):
        tickets_data.file_proposal(
            tmp_db, ticket.id, field=field, body=field, actor="agent", now=21
        )
    _clear(tmp_db, ticket.id)

    tickets_data.edit_field_value(
        tmp_db,
        ticket.id,
        field="success",
        new_body="edited success",
        actor="human",
        now=22,
    )
    assert len(_pending(tmp_db, ticket.id)) == 1
    _clear(tmp_db, ticket.id)

    tickets_data.file_proposal(
        tmp_db, ticket.id, field="closeout", body="closeout draft", actor="agent", now=23
    )
    _clear(tmp_db, ticket.id)
    tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="closeout",
        actor="human",
        now=24,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert _pending(tmp_db, ticket.id) == ()


def test_human_recap_marks_context_but_agent_recap_does_not(tmp_db: Connection) -> None:
    ticket = _ticket(tmp_db)
    tickets_data.file_proposal(
        tmp_db, ticket.id, field="success", body="success", actor="agent", now=28
    )
    tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field="success",
        actor="human",
        now=29,
        edited_body=None,
        next_ceiling="needs_plan",
        at_cap=AtCap.propose,
    )

    tickets_data.write_recap(tmp_db, ticket.id, body="agent recap", actor="agent", now=30)
    assert _pending(tmp_db, ticket.id) == ()

    tickets_data.write_recap(tmp_db, ticket.id, body="human recap", actor="human", now=31)
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
        actor="human",
        now=40,
    )
    assert _pending(tmp_db, ticket.id)

    tickets_data.delete_ticket(tmp_db, ticket.id, actor="human", now=41)

    assert _pending(tmp_db, ticket.id) == ()
