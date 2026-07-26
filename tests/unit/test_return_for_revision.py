"""Returning a proposal for revision: validate, send, and only then write."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation2.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, TicketStatus
from planner.tickets.data import change_scope, create_ticket, file_proposal
from planner.tickets.logic import fields_codec
from planner.worker_context import data as worker_context_data

_AGENT = {"X-Plan-Actor": "agent"}
_CONVERSATION_ID = "conv-revision"


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        # What this file asserts is where the guidance went, so the conversation system
        # has to be one that records its writes and never spawns anything.
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    return app, db_path


def _ticket_with_pending_plan(
    db_path: Path, *, conversation_id: str | None = _CONVERSATION_ID
) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Revise plan",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        change_scope(
            conn,
            ticket.id,
            ceiling="needs_plan",
            at_cap=AtCap.propose,
            actor="human",
            now=0,
        )
        file_proposal(conn, ticket.id, field="success", body="success", actor="agent", now=0)
        file_proposal(conn, ticket.id, field="approach", body="approach", actor="agent", now=0)
        file_proposal(conn, ticket.id, field="plan", body="bad plan", actor="agent", now=0)
        if conversation_id is not None:
            conn.execute(
                "UPDATE tickets SET employee_session_id = ? WHERE id = ?",
                (conversation_id, ticket.id),
            )
        conn.commit()
        return ticket.id
    finally:
        conn.close()


def _start_conversation(app: FastAPI, conversation_id: str = _CONVERSATION_ID) -> None:
    from planner.conversation2.contracts import ConversationStartRequest

    asyncio.run(
        app.state.conversation_system.start_conversation(
            ConversationStartRequest(conversation_id=conversation_id)
        )
    )


def _pending_context(db_path: Path, ticket_id: str, text: str) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        worker_context_data.set_context(conn, ticket_id, "ticket_changed", text)
        conn.commit()
    finally:
        conn.close()


def _context_rows(db_path: Path, ticket_id: str) -> int:
    conn = connect(str(db_path))
    try:
        return len(
            conn.execute(
                "SELECT 1 FROM pending_worker_context WHERE worker_entity_id = ?",
                (ticket_id,),
            ).fetchall()
        )
    finally:
        conn.close()


def test_revision_sends_the_guidance_and_only_then_hands_the_ticket_back(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)
    _pending_context(db_path, ticket_id, "The user renamed the ticket.")

    with TestClient(app) as client:
        _start_conversation(app)
        response = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Make the plan concrete."},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ticket_status"] == TicketStatus.agent.value

    writes = app.state.conversation_system.backend_prompt_writes(_CONVERSATION_ID)
    assert len(writes) == 1
    assert writes[0].sender_label == "owner"
    assert "Make the plan concrete." in writes[0].text
    assert "The user renamed the ticket." in writes[0].text
    assert _context_rows(db_path, ticket_id) == 0

    conn = connect(str(db_path))
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
    finally:
        conn.close()
    assert fields_codec.get_slot(ticket.fields, "plan").proposal is None


def test_a_refused_delivery_changes_nothing(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)
    _pending_context(db_path, ticket_id, "The user renamed the ticket.")
    conn = connect(str(db_path))
    try:
        before = tickets_data.read_ticket(conn, ticket_id)
    finally:
        conn.close()

    with TestClient(app) as client:
        _start_conversation(app)
        app.state.conversation_system.arm_backend_write_failure(_CONVERSATION_ID)
        response = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Make the plan concrete."},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"
    assert response.json()["error"]["detail"]["refusal_reason"] == "write_to_backend_failed"
    # Nothing moved: the proposal is intact and the pending context is still owed.
    assert _context_rows(db_path, ticket_id) == 1
    conn = connect(str(db_path))
    try:
        assert tickets_data.read_ticket(conn, ticket_id) == before
    finally:
        conn.close()


def test_a_busy_worker_takes_the_guidance_as_a_held_message(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)

    with TestClient(app) as client:
        _start_conversation(app)
        asyncio.run(
            app.state.conversation_system.send(
                _CONVERSATION_ID, "already working", sender_label="loop"
            )
        )
        response = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Make the plan concrete."},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ticket_status"] == TicketStatus.agent.value
    # Held rather than written: the busy worker gets it when it frees up.
    assert len(app.state.conversation_system.backend_prompt_writes(_CONVERSATION_ID)) == 1


def test_a_ticket_with_no_conversation_is_refused_before_anything_changes(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path, conversation_id=None)
    conn = connect(str(db_path))
    try:
        before = tickets_data.read_ticket(conn, ticket_id)
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Revise it."},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"
    conn = connect(str(db_path))
    try:
        assert tickets_data.read_ticket(conn, ticket_id) == before
    finally:
        conn.close()


def test_a_ticket_whose_worker_already_has_it_is_refused(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (TicketStatus.agent.value, ticket_id),
        )
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        _start_conversation(app)
        response = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Revise it."},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert app.state.conversation_system.backend_prompt_writes(_CONVERSATION_ID) == ()


def test_return_for_revision_agent_forbidden_and_requires_message(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)

    with TestClient(app) as client:
        _start_conversation(app)
        agent = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "x"},
            headers=_AGENT,
        )
        empty = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision", json={"message": ""}
        )

    assert agent.status_code == 400
    assert agent.json()["error"]["code"] == "agent_forbidden"
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "validation"
    assert app.state.conversation_system.backend_prompt_writes(_CONVERSATION_ID) == ()
