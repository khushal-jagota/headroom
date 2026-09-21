"""The two doors a person opens on a Ticket's conversation.

Talking to the worker, and cutting the Ticket loose from the conversation it is in. There
is no door that makes a conversation: a Ticket nobody has spoken to has none, and the
message is what brings one into being. The conversation system here is the in-memory
reference implementation, so what was actually done to the conversation is read from its
own record rather than from the route's report of itself.
"""

from __future__ import annotations

import base64
from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from tests.support.principals import OWNER_PRINCIPAL

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
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=_TITLE_MAX_CHARS,
        )
        conn.commit()
        return ticket.id
    finally:
        conn.close()


def _send(
    client: TestClient,
    ticket_id: str,
    text: str,
    *,
    conversation_id: str | None = None,
    model: str | None = None,
    sender_message_id: str | None = None,
    sent_at_unix_milliseconds: int | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Say something to this Ticket's worker, the way the panel does."""
    body: dict[str, object] = {
        "conversation_id": conversation_id,
        "content": [{"piece": "text", "text": text}],
        "sender_label": "owner",
    }
    if model is not None:
        body["model"] = model
    if sender_message_id is not None:
        body["sender_message_id"] = sender_message_id
    if sent_at_unix_milliseconds is not None:
        body["sent_at_unix_milliseconds"] = sent_at_unix_milliseconds
    posted: Response = client.post(
        f"/api/tickets/{ticket_id}/conversation/send", json=body, headers=headers or {}
    )
    return posted


def _conversation_id(db_path: Path, ticket_id: str) -> str | None:
    conn: Connection = connect(str(db_path))
    try:
        return tickets_data.read_ticket(conn, ticket_id).conversation_id
    finally:
        conn.close()


def test_the_first_message_makes_the_conversation_and_goes_into_it(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    assert _conversation_id(db_path, ticket_id) is None

    with TestClient(app) as client:
        response = _send(client, ticket_id, "hello", model="sonnet")

        assert response.status_code == 200, response.text
        answered = response.json()
        assert answered["fate"] == "started"
        made = answered["conversation_id"]
        assert made is not None
        assert made.startswith(CONVERSATION_ID_PREFIX)
        # The Ticket names it, and the conversation system has the words: both halves.
        assert _conversation_id(db_path, ticket_id) == made
        delivered = [
            observation.text
            for observation in app.state.conversation_system.observations(made)
            if observation.kind is InMemoryConversationObservationKind.prompt_delivered
        ]
        assert delivered == ["hello"]


def test_an_invalid_first_image_makes_neither_conversation_prompt_nor_file(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    malformed_png = b"\x89PNG\r\n\x1a\nnot a structurally valid png"

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket_id}/conversation/send",
            json={
                "conversation_id": None,
                "content": [
                    {
                        "piece": "image",
                        "data": base64.b64encode(malformed_png).decode("ascii"),
                        "media_type": "image/png",
                    }
                ],
                "sender_label": "owner",
            },
        )

    assert response.status_code == 422
    assert _conversation_id(db_path, ticket_id) is None
    assert not list((db_path.parent / "files" / "conversations").glob("**/*"))


def test_a_message_naming_a_conversation_the_ticket_is_not_in_is_turned_away(
    tmp_path: Path,
) -> None:
    """A tab that missed a New must not go on talking to what New killed."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        left_behind = _send(client, ticket_id, "hello").json()["conversation_id"]
        client.post(f"/api/tickets/{ticket_id}/conversation/reset")

        response = _send(client, ticket_id, "still typing", conversation_id=left_behind)

    assert response.status_code == 404, response.text


def test_resetting_kills_the_conversation_and_unlinks_it(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        started = _send(client, ticket_id, "hello").json()["conversation_id"]
        response = client.post(f"/api/tickets/{ticket_id}/conversation/reset")

        assert response.status_code == 200, response.text
        assert response.json()["conversation_id"] is None
        assert _conversation_id(db_path, ticket_id) is None

    # And the next message makes a fresh one rather than reaching the killed one.
    with TestClient(app) as client:
        after = _send(client, ticket_id, "starting over").json()
    assert after["conversation_id"] != started


def _start_values(client: TestClient, ticket_id: str) -> dict[str, object]:
    """Ask what a conversation for this Ticket's worker would run on, as the panel does."""
    asked: Response = client.get(f"/api/tickets/{ticket_id}/conversation/start-values")
    assert asked.status_code == 200, asked.text
    answer: dict[str, object] = asked.json()
    return answer


def test_the_first_message_runs_on_what_the_panel_was_shown(tmp_path: Path) -> None:
    """The read and the create are the same resolve, so they cannot say different things."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)
    conn = connect(str(db_path))
    try:
        with conn:
            conn.execute(
                "UPDATE tickets SET employee_backend = ?, employee_launch_model = ?, "
                "employee_launch_reasoning_effort = NULL WHERE id = ?",
                ("hermes", "openai-codex:gpt-5.6-sol", ticket_id),
            )
    finally:
        conn.close()

    with TestClient(app) as client:
        shown = _start_values(client, ticket_id)
        conversation_id = _send(client, ticket_id, "hello").json()["conversation_id"]
        conversations = app.state.conversation_system

    assert conversations.backend_model(conversation_id) == shown["model"]
    assert shown["model"] == "openai-codex:gpt-5.6-sol"
    assert conversations.backend_reasoning_effort(conversation_id) == shown["reasoning_effort"]
    assert shown["reasoning_effort"] is None


def test_both_doors_are_human_only(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        start = _send(client, ticket_id, "hello", headers=_AGENT)
        reset = client.post(f"/api/tickets/{ticket_id}/conversation/reset", headers=_AGENT)

    assert start.json()["error"]["code"] == "agent_forbidden", start.text
    assert reset.json()["error"]["code"] == "agent_forbidden", reset.text
    assert _conversation_id(db_path, ticket_id) is None
