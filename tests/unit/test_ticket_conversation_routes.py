"""The two doors a person opens on a Ticket's conversation.

Talking to the worker, and cutting the Ticket loose from the conversation it is in. There
is no door that makes a conversation: a Ticket nobody has spoken to has none, and the
message is what brings one into being. The conversation system here is the in-memory
reference implementation, so what was actually done to the conversation is read from its
own record rather than from the route's report of itself.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from sqlite3 import Connection

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


def test_a_second_message_goes_into_the_conversation_the_first_one_made(
    tmp_path: Path,
) -> None:
    """One conversation per Ticket: talking again must not strand the one being talked to."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        first = _send(client, ticket_id, "hello").json()
        again = _send(client, ticket_id, "again", conversation_id=first["conversation_id"])

    assert again.json()["conversation_id"] == first["conversation_id"]


def test_the_senders_own_facts_about_a_message_reach_the_conversation(
    tmp_path: Path,
) -> None:
    """The name a browser minted, and the instant it sent, arrive with the message.

    A browser draws a message the moment Enter is pressed and stops drawing it when the
    record hands it back — which it can only recognise by the name it minted. This door is
    the only thing standing between the two, so what it does with those values is the whole
    of whether that ever works.

    Both deliveries are asserted. The message that makes a conversation and the message
    that joins one already there travel by different paths, and a door that carries the
    name down one of them says nothing about the other.
    """
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        conversations = app.state.conversation_system
        made = _send(
            client,
            ticket_id,
            "the first thing",
            sender_message_id="minted-first",
            sent_at_unix_milliseconds=1_700_000_000_123,
        ).json()["conversation_id"]
        # The turn the first message started, out of the way: a second message sent while
        # the agent is busy is held rather than delivered, which is a different question.
        conversations.complete_running_turn(made)
        _send(
            client,
            ticket_id,
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


def test_a_message_from_a_sender_that_minted_nothing_is_sent_as_it_always_was(
    tmp_path: Path,
) -> None:
    """The readiness loop mints neither, and a message without them is an ordinary one."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        made = _send(client, ticket_id, "no name on this").json()["conversation_id"]

    delivered = [
        observation
        for observation in app.state.conversation_system.observations(made)
        if observation.kind is InMemoryConversationObservationKind.prompt_delivered
    ]
    assert [observation.text for observation in delivered] == ["no name on this"]
    assert delivered[0].sender_message_id is None
    assert delivered[0].sent_at_unix_milliseconds is None


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
        conversation_id = _send(client, ticket_id, "working").json()["conversation_id"]
        conversations = app.state.conversation_system
        asyncio.run(
            conversations.send(
                conversation_id,
                text_message_content("and this"),
                sender_label="loop",
            )
        )
        assert asyncio.run(conversations.is_running(conversation_id)) is True

        client.post(f"/api/tickets/{ticket_id}/conversation/reset")

        assert asyncio.run(conversations.is_running(conversation_id)) is False
        kinds = [
            observation.kind for observation in conversations.observations(conversation_id)
        ]
        assert InMemoryConversationObservationKind.turn_ended in kinds
        assert InMemoryConversationObservationKind.prompt_discarded in kinds


def _start_values(client: TestClient, ticket_id: str) -> dict[str, object]:
    """Ask what a conversation for this Ticket's worker would run on, as the panel does."""
    asked: Response = client.get(f"/api/tickets/{ticket_id}/conversation/start-values")
    assert asked.status_code == 200, asked.text
    answer: dict[str, object] = asked.json()
    return answer


def test_a_ticket_nobody_has_run_says_what_its_worker_type_launches_on(
    tmp_path: Path,
) -> None:
    """The panel's question before there is anything to look at, and asking starts nothing."""
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        assert _start_values(client, ticket_id) == {
            "backend_key": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
        }
        _start_values(client, ticket_id)

    assert _conversation_id(db_path, ticket_id) is None


def test_after_new_it_says_what_that_ticket_last_ran_on(tmp_path: Path) -> None:
    """A Ticket's own last-chosen values outlive the conversation they were chosen in.

    So the panel opens on what this Ticket ran on rather than on what its Worker type
    ships with — which is the whole point of the Ticket having columns of its own.
    """
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        _send(client, ticket_id, "run it on this instead", model="gpt-5.6-codex")
        client.post(f"/api/tickets/{ticket_id}/conversation/reset")

        assert _start_values(client, ticket_id) == {
            "backend_key": "codex",
            "model": "gpt-5.6-codex",
            "reasoning_effort": "medium",
        }


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
            at_cap=AtCap.user_review,
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
    assert _ticket_status(db_path, ticket_id) == "awaiting_user_review"

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
    assert _ticket_status(db_path, ticket_id) == "awaiting_user_review"
