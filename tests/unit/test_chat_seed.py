"""Canonical server-owned Chat turn, history, state, pause, and status coverage."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.chat import data as chat_data
from planner.chat.contracts import (
    ChatActivityObservation,
    ChatHistory,
    ChatMessage,
    GatewayStatus,
    HumanChatCompletion,
    HumanChatObservation,
    HumanChatOutputDelta,
)
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.adapters.base import HumanSessionKeyBinder
from planner.core.adapters.registry import Adapters, build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.shared_gateway import SharedGateway
from planner.tickets.contracts import NO_FURTHER, AtCap, TicketStatus
from planner.tickets.data import accept_proposal, create_ticket


def _make_app(tmp_path: Path, gateway: str = "fake") -> tuple[object, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": gateway,  # "fake" -> echo, "offline" -> offline
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn, worker_type="coding", title="Chat me.", actor="human", now=0, title_max_chars=200
        )
        ticket = accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
    finally:
        conn.close()
    return ticket.id


def _replace_gateway(app: object, gateway: object) -> None:
    app.state.adapters = Adapters(gateway=gateway)  # type: ignore[arg-type]


def _ticket_status(db_path: Path, ticket_id: str) -> str:
    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT ticket_status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()
    return str(row["ticket_status"])


def _set_ticket_status(db_path: Path, ticket_id: str, status: TicketStatus) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET ticket_status = ? WHERE id = ?", (status.value, ticket_id))
    finally:
        conn.close()


def _events(db_path: Path, entity_id: str, kind: str) -> list[dict[str, object]]:
    conn = connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT payload FROM events WHERE entity_id = ? AND kind = ? ORDER BY id",
            (entity_id, kind),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row["payload"]) for row in rows]


def _stored_key(db_path: Path, table: str, entity_id: str) -> object:
    conn = connect(str(db_path))
    try:
        row = conn.execute(
            f"SELECT chat_session_key FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row["chat_session_key"]


def _set_stored_key(db_path: Path, table: str, entity_id: str, key: str) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(f"UPDATE {table} SET chat_session_key = ? WHERE id = ?", (key, entity_id))
    finally:
        conn.close()


def _wait_for_settled(client: TestClient, entity_id: str) -> dict[str, object]:
    for _ in range(40):
        response = client.get(f"/api/chat/{entity_id}/state")
        assert response.status_code == 200
        state = response.json()
        if state["active_turn"] is None:
            return state
        threading.Event().wait(0.05)
    raise AssertionError(state)


def _latest_turn(db_path: Path, entity_id: str) -> dict[str, object]:
    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id, status, session_key, error FROM chat_turns "
            "WHERE entity_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return dict(row)


def test_chat_history_empty_without_session(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    with TestClient(app) as client:
        response = client.get(f"/api/chat/{tid}/history")

    assert response.status_code == 200
    assert response.json() == {"messages": [], "session_key": None}
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


def test_chat_history_reads_full_fake_trace(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    with TestClient(app) as client:
        first = client.post(
            f"/api/chat/{tid}/turns", json={"text": "hello", "mode": "message"}
        )
        assert first.status_code == 200
        _wait_for_settled(client, tid)
        second = client.post(
            f"/api/chat/{tid}/turns", json={"text": "again", "mode": "message"}
        )
        assert second.status_code == 200
        _wait_for_settled(client, tid)
        history = client.get(f"/api/chat/{tid}/history")

    assert history.status_code == 200
    assert history.json() == {
        "messages": [
            {"role": "user", "text": "hello", "created_at": 1},
            {"role": "assistant", "text": "echo: hello", "created_at": 2},
            {"role": "user", "text": "again", "created_at": 3},
            {"role": "assistant", "text": "echo: again", "created_at": 4},
        ],
        "session_key": "fake-sess-1",
    }


def test_chat_state_records_server_owned_human_turn(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    with TestClient(app) as client:
        started = client.post(f"/api/chat/{tid}/turns", json={"text": "hello", "mode": "message"})
        assert started.status_code == 200
        for _ in range(20):
            state = client.get(f"/api/chat/{tid}/state")
            assert state.status_code == 200
            body = state.json()
            if body["active_turn"] is None and len(body["messages"]) >= 2:
                break
            threading.Event().wait(0.05)
        else:
            raise AssertionError(body)

    assert body["messages"] == [
        {
            "id": 1,
            "role": "human",
            "text": "hello",
            "created_at": body["messages"][0]["created_at"],
            "turn_id": body["messages"][0]["turn_id"],
        },
        {
            "id": 2,
            "role": "assistant",
            "text": "echo: hello",
            "created_at": body["messages"][1]["created_at"],
            "turn_id": body["messages"][1]["turn_id"],
        },
    ]
    assert body["messages"][0]["turn_id"] == body["messages"][1]["turn_id"]
    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]
    assert _events(db_path, tid, "chat_turn_started")
    assert _events(db_path, tid, "chat_turn_finished") == [
        {"turn_id": body["messages"][0]["turn_id"], "status": "complete"}
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": 123},
        {"text": "hello", "mode": "message"},
    ],
)
def test_chief_message_endpoint_requires_exact_nonblank_text(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post("/api/messages/chief", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_chief_message_endpoint_starts_chief_message_turn(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    text = "  hello chief\nwith preserved spacing  "

    with TestClient(app) as client:
        response = client.post("/api/messages/chief", json={"text": text})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entity_id"] == CHIEF_OF_STAFF_ENTITY_ID
    assert body["origin"] == "human"
    assert body["mode"] == "message"
    assert body["status"] == "running"
    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT role, text, turn_id FROM chat_messages WHERE entity_id = ?",
            (CHIEF_OF_STAFF_ENTITY_ID,),
        ).fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("human", text, body["id"])


def test_chief_message_endpoint_preserves_already_running_409(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        chat_data.start_turn(
            conn,
            CHIEF_OF_STAFF_ENTITY_ID,
            origin="human",
            mode="message",
            visible_role="human",
            visible_text="still running",
            output_role="assistant",
            phase="thinking",
            activity_label="Thinking",
            now=1,
        )
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post("/api/messages/chief", json={"text": "second"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"


def test_chat_state_shows_active_turn_while_gateway_is_running(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    release = threading.Event()

    class BlockingGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
            return ChatHistory(messages=(), session_key=session_key)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
        ) -> Iterator[HumanChatObservation]:
            bind_session_key("blocked-session")
            yield HumanChatOutputDelta("partial")
            release.wait(2.0)
            yield HumanChatCompletion("partial done", "assistant")

        def catalog(self):  # noqa: ANN201
            raise AssertionError("unused")

    _replace_gateway(app, BlockingGateway())
    with TestClient(app) as client:
        started = client.post(f"/api/chat/{tid}/turns", json={"text": "hold", "mode": "message"})
        assert started.status_code == 200
        for _ in range(20):
            state = client.get(f"/api/chat/{tid}/state").json()
            active = state["active_turn"]
            if active is not None and active["output_text"] == "partial":
                break
            threading.Event().wait(0.05)
        else:
            raise AssertionError(state)
        assert state["messages"][0]["role"] == "human"
        assert active["status"] == "running"
        assert active["phase"] == "responding"
        assert active["session_key"] == "blocked-session"
        assert _stored_key(db_path, "tickets", tid) == "blocked-session"
        release.set()
        for _ in range(20):
            final = client.get(f"/api/chat/{tid}/state").json()
            if final["active_turn"] is None and len(final["messages"]) == 2:
                break
            threading.Event().wait(0.05)
        else:
            raise AssertionError(final)
    assert final["messages"][1]["text"] == "partial done"


def test_chat_state_shows_active_turn_activity_label(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    release = threading.Event()

    class ActivityGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
            return ChatHistory(messages=(), session_key=session_key)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
        ) -> Iterator[HumanChatObservation]:
            bind_session_key("activity-session")
            yield ChatActivityObservation(
                category="tool",
                label="Checking workspace tools",
                lifecycle_state="running",
            )
            release.wait(2.0)
            yield HumanChatCompletion("done", "assistant")

        def catalog(self):  # noqa: ANN201
            raise AssertionError("unused")

    _replace_gateway(app, ActivityGateway())
    with TestClient(app) as client:
        started = client.post(f"/api/chat/{tid}/turns", json={"text": "status", "mode": "message"})
        assert started.status_code == 200
        state = client.get(f"/api/chat/{tid}/state").json()
        active = state["active_turn"]
        for _ in range(20):
            state = client.get(f"/api/chat/{tid}/state").json()
            active = state["active_turn"]
            if active is not None and active["activity_label"] == "Checking workspace tools":
                break
            threading.Event().wait(0.05)
        else:
            raise AssertionError(state)
        assert active["status"] == "running"
        assert active["phase"] == "doing"
        release.set()


def test_pause_active_turn_interrupts_chat_without_touching_ticket_status(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_ticket_status(db_path, tid, TicketStatus.agent_running_step)
    conn = connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO chat_turns ("
            "id, entity_id, origin, mode, status, phase, activity_label, output_role, "
            "output_text, session_key, error, started_at, updated_at, completed_at"
            ") VALUES ('run_pause_test', ?, 'worker', 'worker_step', 'running', "
            "'responding', NULL, 'assistant', 'partial output', 'pause-session', "
            "NULL, 1, 1, NULL)",
            (tid,),
        )
    finally:
        conn.close()

    class InterruptGateway:
        interrupt_calls: list[tuple[str, str]] = []

        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
            return ChatHistory(messages=(), session_key=session_key)

        def interrupt(self, session_key: str, entity_id: str) -> None:
            self.interrupt_calls.append((session_key, entity_id))

    gateway = InterruptGateway()
    _replace_gateway(app, gateway)

    with TestClient(app) as client:
        paused = client.post(f"/api/chat/{tid}/pause")
        state = client.get(f"/api/chat/{tid}/state").json()

    assert paused.status_code == 200
    assert paused.json()["status"] == "interrupted"
    assert gateway.interrupt_calls == [("pause-session", tid)]
    assert _ticket_status(db_path, tid) == TicketStatus.agent_running_step.value
    assert state["active_turn"] is None
    assert [(msg["role"], msg["text"]) for msg in state["messages"]] == [
        ("assistant", "partial output")
    ]
    assert _events(db_path, tid, "chat_turn_finished") == [
        {"turn_id": "run_pause_test", "status": "interrupted"}
    ]


@pytest.mark.parametrize("entity_kind", ["ticket", "day", "chief"])
def test_pause_then_immediate_human_turn_keeps_late_old_completion_out_of_new_turn(
    tmp_path: Path,
    entity_kind: str,
) -> None:
    app, db_path = _make_app(tmp_path)
    if entity_kind == "ticket":
        entity_id = _ticket(db_path)
    elif entity_kind == "day":
        entity_id = "day_2026-07-04"
    else:
        entity_id = CHIEF_OF_STAFF_ENTITY_ID
    live_session_id = "live-human-session"
    stored_key = "20260710_120000_human"
    fake = FakeGateway(
        {
            "session.create": [
                Reply(
                    result={
                        "session_id": live_session_id,
                        "stored_session_id": stored_key,
                    }
                )
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(ev("message.start", live_session_id),),
                ),
                Reply(
                    result={"status": "queued"},
                    events_after=(
                        ev(
                            "message.complete",
                            live_session_id,
                            {"status": "interrupted", "text": "old completion"},
                        ),
                        ev("message.start", live_session_id),
                        ev("message.delta", live_session_id, {"text": "new"}),
                        ev(
                            "message.complete",
                            live_session_id,
                            {"status": "complete", "text": "new reply"},
                        ),
                    ),
                ),
            ],
            "session.interrupt": [Reply(result={"interrupted": True})],
        }
    )
    gateway = SharedGateway(
        hermes_python="/x/hermes-agent/venv/bin/python",
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
    )
    _replace_gateway(app, gateway)

    with TestClient(app) as client:
        first = client.post(
            f"/api/chat/{entity_id}/turns",
            json={"text": "first", "mode": "message"},
        )
        assert first.status_code == 200
        assert fake.wait_sent(2, 5.0)
        paused = client.post(f"/api/chat/{entity_id}/pause")
        assert paused.status_code == 200
        assert paused.json()["status"] == "interrupted"
        second = client.post(
            f"/api/chat/{entity_id}/turns",
            json={"text": "second", "mode": "message"},
        )
        assert second.status_code == 200
        for _ in range(40):
            body = client.get(f"/api/chat/{entity_id}/state").json()
            if body["active_turn"] is None and any(
                message["text"] == "new reply" for message in body["messages"]
            ):
                break
            threading.Event().wait(0.05)
        else:
            raise AssertionError(body)

    assert [(message["role"], message["text"]) for message in body["messages"]] == [
        ("human", "first"),
        ("human", "second"),
        ("assistant", "new reply"),
    ]
    assert "old completion" not in str(body)
    assert fake.sent_methods() == [
        "session.create",
        "prompt.submit",
        "session.interrupt",
        "prompt.submit",
    ]


def test_chat_history_rejects_agents(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    with TestClient(app) as client:
        response = client.get(f"/api/chat/{tid}/history", headers={"X-Plan-Actor": "agent"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_chat_history_offline_is_503_when_session_exists(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path, gateway="offline")
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "stored-key")

    with TestClient(app) as client:
        response = client.get(f"/api/chat/{tid}/history")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"
    assert _stored_key(db_path, "tickets", tid) == "stored-key"


def test_chat_history_rotated_key_repersists(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "old-key")

    class RotatingHistoryGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
            assert session_key == "old-key"
            assert entity_id == tid
            return ChatHistory(
                messages=(ChatMessage(role="system", text="worker prompt", created_at=7),),
                session_key="fresh-key",
            )

    _replace_gateway(app, RotatingHistoryGateway())
    with TestClient(app) as client:
        response = client.get(f"/api/chat/{tid}/history")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [{"role": "system", "text": "worker prompt", "created_at": 7}],
        "session_key": "fresh-key",
    }
    assert _stored_key(db_path, "tickets", tid) == "fresh-key"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fresh-key"}]


def test_second_human_turn_reuses_key_without_new_event(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        first = client.post(
            f"/api/chat/{tid}/turns", json={"text": "hello", "mode": "message"}
        )
        assert first.status_code == 200
        _wait_for_settled(client, tid)
        second = client.post(
            f"/api/chat/{tid}/turns", json={"text": "again", "mode": "message"}
        )
        assert second.status_code == 200
        state = _wait_for_settled(client, tid)

    assert [(message["role"], message["text"]) for message in state["messages"]] == [
        ("human", "hello"),
        ("assistant", "echo: hello"),
        ("human", "again"),
        ("assistant", "echo: again"),
    ]
    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]


def test_human_turn_offline_settles_failed_without_key(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path, gateway="offline")
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "hello", "mode": "message"}
        )
        assert response.status_code == 200
        _wait_for_settled(client, tid)

    assert response.status_code == 200
    assert _latest_turn(db_path, tid)["status"] == "errored"
    assert _latest_turn(db_path, tid)["error"] == "gateway offline"
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


@pytest.mark.parametrize("entity_kind", ["ticket", "day", "chief"])
def test_human_turn_supports_each_chattable_entity(tmp_path: Path, entity_kind: str) -> None:
    app, db_path = _make_app(tmp_path)
    if entity_kind == "ticket":
        entity_id = _ticket(db_path)
        table = "tickets"
    elif entity_kind == "day":
        entity_id = "day_2026-07-04"
        table = "days"
    else:
        entity_id = CHIEF_OF_STAFF_ENTITY_ID
        table = "agent_chat_sessions"

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{entity_id}/turns",
            json={"text": "plan check", "mode": "message"},
        )
        assert response.status_code == 200
        _wait_for_settled(client, entity_id)

    assert response.status_code == 200
    assert _stored_key(db_path, table, entity_id) == "fake-sess-1"
    if entity_kind == "day":
        assert _events(db_path, entity_id, "day_created") == [{}]
    assert _events(db_path, entity_id, "chat_session_created") == [
        {"session_key": "fake-sess-1"}
    ]


def test_chat_history_chief_of_staff_empty_without_ticket_or_day(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get(f"/api/chat/{CHIEF_OF_STAFF_ENTITY_ID}/history")

    assert response.status_code == 200
    assert response.json() == {"messages": [], "session_key": None}
    assert _stored_key(db_path, "agent_chat_sessions", CHIEF_OF_STAFF_ENTITY_ID) is None
    assert _events(db_path, CHIEF_OF_STAFF_ENTITY_ID, "chat_session_created") == []


@pytest.mark.parametrize("entity_id", ["agent_other", "t_missing", "xyz", "day_bogus"])
def test_human_turn_rejects_missing_or_invalid_entity(
    tmp_path: Path, entity_id: str
) -> None:
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{entity_id}/turns", json={"text": "x", "mode": "message"}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_chat_status_offline_false(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path, gateway="offline")
    with TestClient(app) as client:
        response = client.get("/api/chat/t_anything/status")
    assert response.status_code == 200
    assert response.json() == {"available": False}


def test_chat_status_echo_true(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/chat/t_anything/status")
    assert response.status_code == 200
    assert response.json() == {"available": True}


def test_human_turn_lost_first_write_race_adopts_winner_key(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    class RacingGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
        ) -> Iterator[HumanChatObservation]:
            other = connect(str(db_path))
            try:
                other.execute("BEGIN IMMEDIATE")
                other.execute(
                    "UPDATE tickets SET chat_session_key = ? WHERE id = ?",
                    ("winner-key", entity_id),
                )
                other.execute("COMMIT")
            finally:
                other.close()
            bind_session_key("loser-key")
            yield HumanChatCompletion("echo: x", "assistant")

    _replace_gateway(app, RacingGateway())
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "x", "mode": "message"}
        )
        assert response.status_code == 200
        _wait_for_settled(client, tid)

    assert _stored_key(db_path, "tickets", tid) == "winner-key"
    assert _latest_turn(db_path, tid)["session_key"] == "winner-key"
    assert _events(db_path, tid, "chat_session_created") == []


def test_human_turn_stale_session_remints_and_repersists(tmp_path: Path) -> None:
    """A stored key the gateway no longer has: the real adapter mints a fresh session
    (4007 -> create) and returns a new key. The service must replace the stale key and
    log the new one, so the dead key is never resumed again (the 'Gateway Offline' bug)."""
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    seed = connect(str(db_path))  # a prior session the gateway has since forgotten
    try:
        seed.execute("BEGIN IMMEDIATE")
        seed.execute("UPDATE tickets SET chat_session_key = ? WHERE id = ?", ("stale-key", tid))
        seed.execute("COMMIT")
    finally:
        seed.close()

    class ReMintGateway:
        """Returns a fresh key for the stale one — as the real adapter does after 4007."""

        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
        ) -> Iterator[HumanChatObservation]:
            assert session_key == "stale-key"  # the stale key is passed through
            bind_session_key("fresh-key")
            yield HumanChatCompletion("echo: x", "assistant")

    _replace_gateway(app, ReMintGateway())
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "x", "mode": "message"}
        )
        assert response.status_code == 200
        _wait_for_settled(client, tid)

    assert _stored_key(db_path, "tickets", tid) == "fresh-key"
    assert _latest_turn(db_path, tid)["session_key"] == "fresh-key"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fresh-key"}]


def test_ticket_chat_does_not_change_ticket_status(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    statuses = [
        TicketStatus.empty,
        TicketStatus.awaiting_approval,
        TicketStatus.user_takeover,
    ]
    tids: list[str] = []
    for status in statuses:
        tid = _ticket(db_path)
        _set_ticket_status(db_path, tid, status)
        tids.append(tid)

    with TestClient(app) as client:
        for tid, status in zip(tids, statuses, strict=True):
            response = client.post(
                f"/api/chat/{tid}/turns", json={"text": "hello", "mode": "message"}
            )
            assert response.status_code == 200
            _wait_for_settled(client, tid)
            assert _ticket_status(db_path, tid) == status.value


def test_human_turn_rejects_while_worker_step_running(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_ticket_status(db_path, tid, TicketStatus.agent_running_step)

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "hello", "mode": "message"}
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


def test_human_turn_gateway_busy_settles_failed(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    class BusyGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
        ) -> Iterator[HumanChatObservation]:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id},
            )

    _replace_gateway(app, BusyGateway())
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "hello", "mode": "message"}
        )
        assert response.status_code == 200
        _wait_for_settled(client, tid)

    assert _latest_turn(db_path, tid)["status"] == "errored"
    assert _latest_turn(db_path, tid)["error"] == "an agent is already running on this ticket"
    assert _ticket_status(db_path, tid) == TicketStatus.empty.value


def test_literal_new_turn_starts_fresh_session_reused_by_next_message(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    live_session_id = "new-live-session"
    stored_key = "20260714_120000_new"
    fake = FakeGateway(
        {
            "session.create": [
                Reply(
                    result={
                        "session_id": live_session_id,
                        "stored_session_id": stored_key,
                    }
                )
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(
                        ev("message.start", live_session_id),
                        ev("message.delta", live_session_id, {"text": "hello reply"}),
                        ev(
                            "message.complete",
                            live_session_id,
                            {"status": "complete", "text": "hello reply"},
                        ),
                    ),
                )
            ],
        }
    )
    gateway = SharedGateway(
        hermes_python="/x/hermes-agent/venv/bin/python",
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
    )
    _replace_gateway(app, gateway)

    try:
        with TestClient(app) as client:
            command = client.post(
                f"/api/chat/{tid}/turns",
                json={"text": "/new", "mode": "command"},
            )
            assert command.status_code == 200
            command_state = _wait_for_settled(client, tid)
            assert command_state["messages"][-1]["role"] == "system"
            assert command_state["messages"][-1]["text"] == "New session started."
            assert _stored_key(db_path, "tickets", tid) == stored_key

            message = client.post(
                f"/api/chat/{tid}/turns",
                json={"text": "hello new session", "mode": "message"},
            )
            assert message.status_code == 200
            message_state = _wait_for_settled(client, tid)
    finally:
        gateway.shutdown()

    assert message_state["messages"][-1]["text"] == "hello reply"
    assert _stored_key(db_path, "tickets", tid) == stored_key
    assert fake.sent_methods() == ["session.create", "prompt.submit"]
    assert fake.sent[1]["params"] == {
        "session_id": live_session_id,
        "text": "hello new session",
    }


def test_new_with_arguments_uses_ordinary_command_dispatch(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    live_session_id = "generic-live-session"
    stored_key = "20260714_120000_generic"
    fake = FakeGateway(
        {
            "session.create": [
                Reply(
                    result={
                        "session_id": live_session_id,
                        "stored_session_id": stored_key,
                    }
                )
            ],
            "slash.exec": [Reply(result={"type": "exec", "output": "generic output"})],
        }
    )
    gateway = SharedGateway(
        hermes_python="/x/hermes-agent/venv/bin/python",
        home="/tmp/planner-home",
        worker_role="planner-worker",
        spawn=fake.spawn,
        base_env={},
    )
    _replace_gateway(app, gateway)

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/chat/{tid}/turns",
                json={"text": "/new title", "mode": "command"},
            )
            assert response.status_code == 200
            state = _wait_for_settled(client, tid)
    finally:
        gateway.shutdown()

    assert state["messages"][-1]["role"] == "system"
    assert state["messages"][-1]["text"] == "generic output"
    assert _stored_key(db_path, "tickets", tid) == stored_key
    assert fake.sent_methods() == ["session.create", "slash.exec"]
