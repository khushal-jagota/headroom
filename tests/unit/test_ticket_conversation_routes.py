"""The two doors a person opens on a Ticket's conversation.

The readiness loop starts a Ticket's conversation when it has a step to send. These are
the doors the owner uses instead: start one on a Ticket nobody has run yet, and cut a
Ticket loose from the one it has. The conversation system here is the in-memory reference
implementation, so what was actually done to the conversation is read from its own record
rather than from the route's report of itself.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.runtime.conversation_start import CONVERSATION_ID_PREFIX
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap

_AGENT = {"X-Plan-Actor": "agent"}
_TITLE_MAX_CHARS = 200


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
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    return app, db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Talk to it first",
            actor="human",
            now=0,
            title_max_chars=_TITLE_MAX_CHARS,
        )
        conn.commit()
        return ticket.id
    finally:
        conn.close()


def _conversation_id(db_path: Path, ticket_id: str) -> str | None:
    conn: Connection = connect(str(db_path))
    try:
        return tickets_data.read_ticket(conn, ticket_id).conversation_id
    finally:
        conn.close()


def test_starting_gives_a_ticket_that_never_ran_a_conversation_to_talk_to(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    assert _conversation_id(db_path, ticket_id) is None

    with TestClient(app) as client:
        response = client.post(f"/api/tickets/{ticket_id}/conversation")

        assert response.status_code == 200, response.text
        started = response.json()["conversation_id"]
        assert started is not None
        assert started.startswith(CONVERSATION_ID_PREFIX)
        # The Ticket names it, and the conversation system has it: both halves of the link.
        assert _conversation_id(db_path, ticket_id) == started
        assert app.state.conversation_system.observations(started) == ()


def test_a_ticket_that_already_has_a_conversation_keeps_the_one_it_has(
    tmp_path: Path,
) -> None:
    """Starting twice must not strand a live conversation nothing can reach any more."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        first = client.post(f"/api/tickets/{ticket_id}/conversation").json()
        again = client.post(f"/api/tickets/{ticket_id}/conversation").json()

    assert again["conversation_id"] == first["conversation_id"]


def test_resetting_kills_the_conversation_and_unlinks_it(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        started = client.post(f"/api/tickets/{ticket_id}/conversation").json()[
            "conversation_id"
        ]
        response = client.post(f"/api/tickets/{ticket_id}/conversation/reset")

        assert response.status_code == 200, response.text
        assert response.json()["conversation_id"] is None
        assert _conversation_id(db_path, ticket_id) is None

    # And the next start is a fresh conversation rather than the one that was killed.
    with TestClient(app) as client:
        after = client.post(f"/api/tickets/{ticket_id}/conversation").json()
    assert after["conversation_id"] != started


def test_resetting_a_ticket_with_no_conversation_changes_nothing(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        response = client.post(f"/api/tickets/{ticket_id}/conversation/reset")

    assert response.status_code == 200, response.text
    assert response.json()["conversation_id"] is None


def test_a_running_turn_is_stopped_by_the_reset(tmp_path: Path) -> None:
    """New silences the old worker outright: the turn stops and held messages are dropped."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        conversation_id = client.post(f"/api/tickets/{ticket_id}/conversation").json()[
            "conversation_id"
        ]
        conversations = app.state.conversation_system
        asyncio.run(conversations.send(conversation_id, "working", sender_label="loop"))
        asyncio.run(conversations.send(conversation_id, "and this", sender_label="loop"))
        assert asyncio.run(conversations.is_running(conversation_id)) is True

        client.post(f"/api/tickets/{ticket_id}/conversation/reset")

        assert asyncio.run(conversations.is_running(conversation_id)) is False
        kinds = [
            observation.kind for observation in conversations.observations(conversation_id)
        ]
        assert InMemoryConversationObservationKind.turn_ended in kinds
        assert InMemoryConversationObservationKind.prompt_discarded in kinds


def test_both_doors_are_human_only(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        start = client.post(f"/api/tickets/{ticket_id}/conversation", headers=_AGENT)
        reset = client.post(f"/api/tickets/{ticket_id}/conversation/reset", headers=_AGENT)

    assert start.json()["error"]["code"] == "agent_forbidden", start.text
    assert reset.json()["error"]["code"] == "agent_forbidden", reset.text
    assert _conversation_id(db_path, ticket_id) is None


# --- a person replying to the worker -------------------------------------------------------


def _ticket_status(db_path: Path, ticket_id: str) -> str:
    conn: Connection = connect(str(db_path))
    try:
        return str(tickets_data.read_ticket(conn, ticket_id).ticket_status)
    finally:
        conn.close()


def _past_kickoff(db_path: Path, ticket_id: str) -> None:
    """Accept the kickoff a new Ticket is parked on, leaving it with nothing waiting."""
    conn: Connection = connect(str(db_path))
    try:
        tickets_data.accept_proposal(
            conn,
            ticket_id,
            field="kickoff",
            actor="human",
            now=1,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        conn.commit()
    finally:
        conn.close()


def _park_on_a_proposal(db_path: Path, ticket_id: str) -> None:
    _past_kickoff(db_path, ticket_id)
    conn: Connection = connect(str(db_path))
    try:
        tickets_data.file_proposal(
            conn, ticket_id, field="success", body="how we will know", actor="agent", now=1
        )
        conn.commit()
    finally:
        conn.close()


def test_replying_to_a_parked_proposal_moves_the_ticket_to_paired(tmp_path: Path) -> None:
    """A Ticket waiting for its owner, answered rather than approved."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    _park_on_a_proposal(db_path, ticket_id)
    assert _ticket_status(db_path, ticket_id) == "awaiting_approval"

    with TestClient(app) as client:
        replied = client.post(f"/api/tickets/{ticket_id}/human-reply")

    assert replied.status_code == 200, replied.text
    assert replied.json()["ticket_status"] == "paired"
    assert _ticket_status(db_path, ticket_id) == "paired"


def test_replying_leaves_every_other_status_exactly_as_it_was(tmp_path: Path) -> None:
    """The writer owns which statuses move, and this route reports every reply to it."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    _past_kickoff(db_path, ticket_id)
    assert _ticket_status(db_path, ticket_id) == "empty"

    with TestClient(app) as client:
        replied = client.post(f"/api/tickets/{ticket_id}/human-reply")

    assert replied.status_code == 200, replied.text
    assert replied.json()["ticket_status"] == "empty"
    assert _ticket_status(db_path, ticket_id) == "empty"


def test_only_a_person_can_say_they_replied(tmp_path: Path) -> None:
    """The automatic loop sends into the same conversation; its prompts are not replies."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    _park_on_a_proposal(db_path, ticket_id)

    with TestClient(app) as client:
        refused = client.post(f"/api/tickets/{ticket_id}/human-reply", headers=_AGENT)

    assert refused.json()["error"]["code"] == "agent_forbidden", refused.text
    assert _ticket_status(db_path, ticket_id) == "awaiting_approval"
