from __future__ import annotations

from pathlib import Path

import pytest

from planner.chat import service as chat_service
from planner.chat.contracts import ChatSendResult, ChatStreamChunk, CommandRunResult
from planner.core.contracts import Priority
from planner.core.db import connect
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    FieldName,
    TicketEdit,
    TicketState,
)
from planner.tickets.worker_context import TICKET_CHANGED_CONTEXT_KEY, TICKET_CHANGED_TEXT
from planner.worker_context import data
from planner.worker_context.contracts import WorkerContextReceipt
from planner.worker_context.service import SqliteWorkerContextService


def test_pending_context_coalesces_by_worker_and_key_with_monotonic_revisions(tmp_db) -> None:
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


def test_acknowledge_removes_only_exact_key_revision_pairs(tmp_db) -> None:
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


def test_sqlite_service_composes_prompt_and_acknowledges_only_prepared_revision(tmp_db) -> None:
    db_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()["file"]))
    service = SqliteWorkerContextService(lambda: connect(db_path))
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


def _ticket(tmp_db):
    return tickets_data.create_ticket(
        tmp_db, title="Context ticket", actor="human", now=1, title_max_chars=200
    )


def _pending(tmp_db, ticket_id: str):
    return data.snapshot(tmp_db, ticket_id).items


def _clear(tmp_db, ticket_id: str) -> None:
    snapshot = data.snapshot(tmp_db, ticket_id)
    data.acknowledge(tmp_db, ticket_id, snapshot.receipts)


def test_human_ticket_edits_coalesce_but_agent_writes_do_not_produce_context(tmp_db) -> None:
    ticket = _ticket(tmp_db)

    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(priority=Priority.P1),
        title_max_chars=TITLE_MAX_CHARS,
        actor="agent",
        now=2,
    )
    tickets_data.set_field_user_note(
        tmp_db,
        ticket.id,
        field=FieldName.success,
        user_note="agent note",
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
    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(user_note="ticket guidance"),
        title_max_chars=TITLE_MAX_CHARS,
        actor="human",
        now=5,
    )
    tickets_data.set_field_user_note(
        tmp_db,
        ticket.id,
        field=FieldName.success,
        user_note="field guidance",
        actor="human",
        now=6,
    )

    pending = [
        (item.context_key, item.text, item.revision)
        for item in _pending(tmp_db, ticket.id)
    ]
    assert pending == [
        (TICKET_CHANGED_CONTEXT_KEY, TICKET_CHANGED_TEXT, 3)
    ]


@pytest.mark.parametrize(
    ("field", "prior_fields"),
    [
        (FieldName.success, ()),
        (FieldName.approach, (FieldName.success,)),
        (FieldName.plan, (FieldName.success, FieldName.approach)),
    ],
)
def test_only_edited_approval_produces_context_at_each_approval_gate(
    tmp_db, field: FieldName, prior_fields: tuple[FieldName, ...]
) -> None:
    def parked_ticket(edited: bool):
        ticket = _ticket(tmp_db)
        ceiling = {
            FieldName.success: TicketState.needs_success,
            FieldName.approach: TicketState.needs_approach,
            FieldName.plan: TicketState.needs_plan,
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
                tmp_db, ticket.id, field=prior, body=prior.value, actor="agent", now=11
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


def test_direct_value_and_scope_edits_produce_context_but_review_approval_does_not(tmp_db) -> None:
    ticket = _ticket(tmp_db)
    tickets_data.change_scope(
        tmp_db,
        ticket.id,
        ceiling=TicketState.needs_review,
        at_cap=AtCap.propose,
        actor="human",
        now=20,
    )
    for field in (FieldName.success, FieldName.approach, FieldName.plan, FieldName.result):
        tickets_data.file_proposal(
            tmp_db, ticket.id, field=field, body=field.value, actor="agent", now=21
        )
    _clear(tmp_db, ticket.id)

    tickets_data.edit_field_value(
        tmp_db,
        ticket.id,
        field=FieldName.success,
        new_body="edited success",
        actor="human",
        now=22,
    )
    assert len(_pending(tmp_db, ticket.id)) == 1
    _clear(tmp_db, ticket.id)

    tickets_data.approve_review(tmp_db, ticket.id, actor="human", now=23)
    assert _pending(tmp_db, ticket.id) == ()


def test_human_recap_marks_context_but_agent_recap_does_not(tmp_db) -> None:
    ticket = _ticket(tmp_db)
    tickets_data.file_proposal(
        tmp_db, ticket.id, field=FieldName.success, body="success", actor="agent", now=28
    )
    tickets_data.accept_proposal(
        tmp_db,
        ticket.id,
        field=FieldName.success,
        actor="human",
        now=29,
        edited_body=None,
        next_ceiling=TicketState.needs_plan,
        at_cap=AtCap.propose,
    )

    tickets_data.write_recap(tmp_db, ticket.id, body="agent recap", actor="agent", now=30)
    assert _pending(tmp_db, ticket.id) == ()

    tickets_data.write_recap(tmp_db, ticket.id, body="human recap", actor="human", now=31)
    assert [(item.context_key, item.revision) for item in _pending(tmp_db, ticket.id)] == [
        (TICKET_CHANGED_CONTEXT_KEY, 1)
    ]


def test_deleting_ticket_removes_its_pending_context(tmp_db) -> None:
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


def test_legacy_chat_paths_persist_original_visible_text_without_gateway_history(tmp_db) -> None:
    class Gateway:
        def history(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            raise AssertionError("local visible messages should prevent Hermes history fallback")

        def send(self, session_key, entity_id, text, on_session_key=None):  # noqa: ANN001, ANN201
            return ChatSendResult(reply_text="send reply", session_key=f"session-{entity_id}")

        def run_command(
            self, session_key, entity_id, command, on_session_key=None  # noqa: ANN001
        ) -> CommandRunResult:
            return CommandRunResult(
                reply_text="command reply", session_key=f"session-{entity_id}", kind="assistant"
            )

        def stream(
            self, session_key, entity_id, text, mode, on_session_key=None  # noqa: ANN001
        ):
            key = f"session-{entity_id}"
            yield ChatStreamChunk(type="session", session_key=key)
            yield ChatStreamChunk(
                type="done", reply_text="stream reply", session_key=key, kind="assistant"
            )

    gateway = Gateway()
    send_ticket = _ticket(tmp_db)
    command_ticket = _ticket(tmp_db)
    stream_ticket = _ticket(tmp_db)

    chat_service.send(tmp_db, gateway, send_ticket.id, "original send", 50)  # type: ignore[arg-type]
    chat_service.run_command(
        tmp_db, gateway, command_ticket.id, "/model-backed", 51  # type: ignore[arg-type]
    )
    list(
        chat_service.stream(
            tmp_db, gateway, stream_ticket.id, "original stream", "message", 52  # type: ignore[arg-type]
        )
    )

    assert [
        message.text
        for message in chat_service.state(tmp_db, gateway, send_ticket.id, 53).messages  # type: ignore[arg-type]
    ] == ["original send", "send reply"]
    assert [
        message.text
        for message in chat_service.state(tmp_db, gateway, command_ticket.id, 53).messages  # type: ignore[arg-type]
    ] == ["/model-backed", "command reply"]
    assert [
        message.text
        for message in chat_service.state(tmp_db, gateway, stream_ticket.id, 53).messages  # type: ignore[arg-type]
    ] == ["original stream", "stream reply"]
