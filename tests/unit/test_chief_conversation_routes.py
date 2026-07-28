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
from httpx import Response

from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.conversation.message_content import text_message_content
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


def _send(
    client: TestClient,
    text: str,
    *,
    conversation_id: str | None = None,
    sender_message_id: str | None = None,
    sent_at_unix_milliseconds: int | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Say something to the Chief, the way its panel does."""
    body: dict[str, object] = {
        "conversation_id": conversation_id,
        "content": [{"piece": "text", "text": text}],
        "sender_label": "owner",
    }
    if sender_message_id is not None:
        body["sender_message_id"] = sender_message_id
    if sent_at_unix_milliseconds is not None:
        body["sent_at_unix_milliseconds"] = sent_at_unix_milliseconds
    posted: Response = client.post(
        "/api/chief/conversation/send",
        json=body,
        headers=headers or {},
    )
    return posted


def test_the_senders_own_facts_about_a_message_reach_the_conversation(
    tmp_path: Path,
) -> None:
    """The name a browser minted, and the instant it sent, arrive with the message.

    The Ticket door's test, asked of the Chief's, and for the same reason: the panel stops
    drawing its own copy of a message when the record hands it back under the name it
    minted, and this door is the only thing between the two.

    Both deliveries are asserted, because the message that makes a conversation and the
    message that joins one already there travel by different paths.
    """
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        conversations = app.state.conversation_system
        made = _send(
            client,
            "the first thing",
            sender_message_id="minted-first",
            sent_at_unix_milliseconds=1_700_000_000_123,
        ).json()["conversation_id"]
        # The turn the first message started, out of the way: a second message sent while
        # the agent is busy is held rather than delivered, which is a different question.
        conversations.complete_running_turn(made)
        _send(
            client,
            "the second thing",
            conversation_id=made,
            sender_message_id="minted-second",
            sent_at_unix_milliseconds=1_700_000_000_456,
        )

    assert [
        (observation.text, observation.sender_message_id, observation.sent_at_unix_milliseconds)
        for observation in conversations.observations(made)
        if observation.kind is InMemoryConversationObservationKind.prompt_delivered
    ] == [
        ("the first thing", "minted-first", 1_700_000_000_123),
        ("the second thing", "minted-second", 1_700_000_000_456),
    ]


def test_the_chief_has_no_conversation_until_one_is_started(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        before = client.get("/api/chief/conversation")
        assert before.status_code == 200, before.text
        assert before.json() == {"conversation_id": None}

        started = _send(client, "hello")
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
        first = _send(client, "hello").json()["conversation_id"]
        again = _send(client, "again", conversation_id=first).json()["conversation_id"]

    assert again == first


def test_resetting_silences_the_chief_and_the_next_start_is_a_fresh_one(
    tmp_path: Path,
) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        conversation_id = _send(client, "working").json()["conversation_id"]
        conversations = app.state.conversation_system
        asyncio.run(
            conversations.send(
                conversation_id,
                text_message_content("and this"),
                sender_label="owner",
            )
        )
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

        assert _send(client, "starting over").json()["conversation_id"] != conversation_id


def test_the_agent_outlives_the_conversation_it_was_having(tmp_path: Path) -> None:
    """A row here is an agent, not a link. Resetting lets go of the conversation; the
    agent did not stop existing, so its row is still there holding nothing."""
    app, db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        _send(client, "hello")
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
        start = _send(client, "hello", headers=_AGENT)
        reset = client.post("/api/chief/conversation/reset", headers=_AGENT)
        after = client.get("/api/chief/conversation")

    assert start.json()["error"]["code"] == "agent_forbidden", start.text
    assert reset.json()["error"]["code"] == "agent_forbidden", reset.text
    assert after.json() == {"conversation_id": None}


def test_what_a_conversation_started_now_would_run_on_is_the_chiefs_own(
    tmp_path: Path,
) -> None:
    """The panel's question before there is anything to look at.

    It is answered from the Chief's managed settings, so a panel that has never been
    talked to still shows the backend and model a first message would actually run on.
    """
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        before_anything = client.get("/api/chief/conversation/start-values")
        assert before_anything.status_code == 200, before_anything.text
        assert before_anything.json() == {
            "backend_key": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
        }

        # Configured on the Agents screen, and the answer follows it — this is the whole
        # of the gap: a rail saying codex to a Chief that has been moved to claude.
        moved = client.put(
            "/api/workers/chief-of-staff/launch-defaults",
            json={
                "employee_backend": "claude",
                "employee_launch_model": "sonnet",
                "employee_launch_reasoning_effort": None,
            },
        )
        assert moved.status_code == 200, moved.text

        assert client.get("/api/chief/conversation/start-values").json() == {
            "backend_key": "claude",
            "model": "sonnet",
            "reasoning_effort": None,
        }


def test_asking_what_it_would_start_as_starts_nothing(tmp_path: Path) -> None:
    """Reading the question is not answering it: no conversation is made by asking."""
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        client.get("/api/chief/conversation/start-values")
        client.get("/api/chief/conversation/start-values")

        assert client.get("/api/chief/conversation").json() == {"conversation_id": None}


def test_the_first_message_runs_on_what_the_panel_was_shown(tmp_path: Path) -> None:
    """The read and the create are the same resolve, so they cannot say different things.

    Read from the backend side's account of what its session runs on, which is what the
    conversation was actually started with rather than what it was asked for.
    """
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        client.put(
            "/api/workers/chief-of-staff/launch-defaults",
            json={
                "employee_backend": "claude",
                "employee_launch_model": "opus",
                "employee_launch_reasoning_effort": "high",
            },
        )
        shown = client.get("/api/chief/conversation/start-values").json()
        conversation_id = _send(client, "hello").json()["conversation_id"]
        conversations = app.state.conversation_system

    assert conversations.backend_model(conversation_id) == shown["model"]
    assert conversations.backend_reasoning_effort(conversation_id) == shown["reasoning_effort"]


def test_the_chief_starts_on_its_own_managed_settings(tmp_path: Path) -> None:
    """Its model and effort are the Chief's own, not a Ticket's and not a floor default.

    Read from the backend side's account of what its session runs on, which is the only
    place that says what the conversation was actually started with rather than what it
    was asked for.
    """
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        # A backend session is established when something is first sent, and the first
        # message is also what makes the conversation.
        conversation_id = _send(client, "hello").json()["conversation_id"]
        conversations = app.state.conversation_system

    assert conversations.backend_model(conversation_id) == "gpt-5.6-sol"
    assert conversations.backend_reasoning_effort(conversation_id) == "medium"
