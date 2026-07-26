"""The Chief's conversation: the same two doors a Ticket has, on the same writers.

The Chief owns work and has no Ticket row, so which conversation it is currently talking
in is recorded against the agent instead. Everything else — starting, killing, the
once-only rule — is the Ticket's behaviour, and these tests say so by asserting the same
things about it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation2.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.runtime.conversation_start import CONVERSATION_ID_PREFIX

_AGENT = {"X-Plan-Actor": "agent"}


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


def test_the_chief_has_no_conversation_until_one_is_started(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        before = client.get("/api/chief/conversation")
        assert before.status_code == 200, before.text
        assert before.json() == {"conversation_id": None}

        started = client.post("/api/chief/conversation")
        assert started.status_code == 200, started.text
        conversation_id = started.json()["conversation_id"]
        assert conversation_id is not None
        assert conversation_id.startswith(CONVERSATION_ID_PREFIX)

        # And it is there to be read afterwards, which is what a reload asks.
        assert client.get("/api/chief/conversation").json() == {
            "conversation_id": conversation_id
        }


def test_a_chief_that_already_has_a_conversation_keeps_the_one_it_has(
    tmp_path: Path,
) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        first = client.post("/api/chief/conversation").json()["conversation_id"]
        again = client.post("/api/chief/conversation").json()["conversation_id"]

    assert again == first


def test_resetting_silences_the_chief_and_the_next_start_is_a_fresh_one(
    tmp_path: Path,
) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        conversation_id = client.post("/api/chief/conversation").json()["conversation_id"]
        conversations = app.state.conversation_system
        asyncio.run(conversations.send(conversation_id, "working", sender_label="owner"))
        asyncio.run(conversations.send(conversation_id, "and this", sender_label="owner"))
        assert asyncio.run(conversations.is_running(conversation_id)) is True

        reset = client.post("/api/chief/conversation/reset")
        assert reset.status_code == 200, reset.text
        assert reset.json() == {"conversation_id": None}
        assert client.get("/api/chief/conversation").json() == {"conversation_id": None}

        # Killed, not merely interrupted: the turn stopped and what was held was dropped.
        assert asyncio.run(conversations.is_running(conversation_id)) is False
        kinds = [
            observation.kind for observation in conversations.observations(conversation_id)
        ]
        assert InMemoryConversationObservationKind.turn_ended in kinds
        assert InMemoryConversationObservationKind.prompt_discarded in kinds

        assert client.post("/api/chief/conversation").json()["conversation_id"] != (
            conversation_id
        )


def test_the_agent_outlives_the_conversation_it_was_having(tmp_path: Path) -> None:
    """A row here is an agent, not a link. Resetting lets go of the conversation; the
    agent did not stop existing, so its row is still there holding nothing."""
    app, db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        client.post("/api/chief/conversation")
        client.post("/api/chief/conversation/reset")

    conn = connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT agent_key, conversation_id FROM agents WHERE agent_key = ?",
            ("chief_of_staff",),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0][1] is None


def test_resetting_a_chief_with_no_conversation_changes_nothing(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post("/api/chief/conversation/reset")

    assert response.status_code == 200, response.text
    assert response.json() == {"conversation_id": None}


def test_both_chief_doors_are_human_only(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        start = client.post("/api/chief/conversation", headers=_AGENT)
        reset = client.post("/api/chief/conversation/reset", headers=_AGENT)
        after = client.get("/api/chief/conversation")

    assert start.json()["error"]["code"] == "agent_forbidden", start.text
    assert reset.json()["error"]["code"] == "agent_forbidden", reset.text
    assert after.json() == {"conversation_id": None}


def test_the_chief_starts_on_its_own_managed_settings(tmp_path: Path) -> None:
    """Its model and effort are the Chief's own, not a Ticket's and not a floor default.

    Read from the backend side's account of what its session runs on, which is the only
    place that says what the conversation was actually started with rather than what it
    was asked for.
    """
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        conversation_id = client.post("/api/chief/conversation").json()["conversation_id"]
        conversations = app.state.conversation_system
        # A backend session is established when something is first sent, so the account
        # only exists once there is one.
        asyncio.run(conversations.send(conversation_id, "hello", sender_label="owner"))

    assert conversations.backend_model(conversation_id) == "gpt-5.6-sol"
    assert conversations.backend_reasoning_effort(conversation_id) == "medium"
