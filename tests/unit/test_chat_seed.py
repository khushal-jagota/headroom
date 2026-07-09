"""T13 acceptance: the chat send/status routes, driven through a TestClient over
create_app with fake adapters. Covers echo persistence + one event, key reuse, day
materialization, not_found/validation/offline error codes, and the guarded
first-reply race."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.chat import service
from planner.chat.contracts import (
    ChatHistory,
    ChatMessage,
    ChatSendResult,
    ChatStreamChunk,
    GatewayStatus,
)
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.adapters.registry import Adapters, build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.tickets.contracts import TicketStatus
from planner.tickets.data import create_ticket


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
        ticket = create_ticket(conn, title="Chat me.", actor="human", now=0, title_max_chars=200)
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
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?", (status.value, ticket_id)
        )
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


def test_chat_send_echo_persists_key_and_event(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
    assert response.status_code == 200
    assert response.json() == {"reply_text": "echo: hello", "session_key": "fake-sess-1"}
    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]


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
        first = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
        second = client.post(f"/api/chat/{tid}/send", json={"text": "again"})
        history = client.get(f"/api/chat/{tid}/history")

    assert first.status_code == 200
    assert second.status_code == 200
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
    assert _events(db_path, tid, "chat_turn_started")
    assert _events(db_path, tid, "chat_turn_finished") == [
        {"turn_id": body["messages"][0]["turn_id"], "status": "complete"}
    ]


def test_chat_state_shows_active_turn_while_gateway_is_running(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    release = threading.Event()

    class BlockingGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
            return ChatHistory(messages=(), session_key=session_key)

        def stream(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> Iterator[ChatStreamChunk]:
            if on_session_key is not None:
                on_session_key("blocked-session")
            yield ChatStreamChunk(type="session", session_key="blocked-session")
            yield ChatStreamChunk(type="token", text="partial")
            release.wait(2.0)
            yield ChatStreamChunk(
                type="done",
                reply_text="partial done",
                session_key="blocked-session",
                kind="assistant",
            )

        def catalog(self):  # noqa: ANN201
            raise AssertionError("unused")

        def send(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            raise AssertionError("unused")

        def run_command(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
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

        def stream(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> Iterator[ChatStreamChunk]:
            if on_session_key is not None:
                on_session_key("activity-session")
            yield ChatStreamChunk(type="session", session_key="activity-session")
            yield ChatStreamChunk(type="activity", text="Checking workspace tools")
            release.wait(2.0)
            yield ChatStreamChunk(
                type="done",
                reply_text="done",
                session_key="activity-session",
                kind="assistant",
            )

        def catalog(self):  # noqa: ANN201
            raise AssertionError("unused")

        def send(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            raise AssertionError("unused")

        def run_command(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
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


def test_chat_stream_echo_persists_key_and_event(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        with client.stream(
            "POST", f"/api/chat/{tid}/stream", json={"text": "hello", "mode": "message"}
        ) as response:
            body = "".join(response.iter_text())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: message_start\ndata: {"entity_id":"' + tid + '","mode":"message"}' in body
    assert 'event: token\ndata: {"text":"echo' in body
    assert (
        'event: message_done\ndata: {"reply_text":"echo: hello",'
        '"session_key":"fake-sess-1","kind":"assistant"}'
    ) in body
    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]


def test_chat_stream_persists_session_before_first_token(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    class InspectingGateway:
        def stream(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> Iterator[ChatStreamChunk]:
            assert session_key is None
            if on_session_key is not None:
                on_session_key("early-key")
            yield ChatStreamChunk(type="session", session_key="early-key")
            assert _stored_key(db_path, "tickets", entity_id) == "early-key"
            yield ChatStreamChunk(type="token", text="ready")
            yield ChatStreamChunk(
                type="done",
                reply_text="ready",
                session_key="early-key",
                kind="assistant",
            )

        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

    _replace_gateway(app, InspectingGateway())
    with TestClient(app) as client:
        with client.stream(
            "POST", f"/api/chat/{tid}/stream", json={"text": "hello", "mode": "message"}
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert 'event: token\ndata: {"text":"ready"}' in body
    assert 'event: message_done\ndata: {"reply_text":"ready","session_key":"early-key"' in body
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "early-key"}]


def test_chat_stream_command_uses_system_kind(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        with client.stream(
            "POST", f"/api/chat/{tid}/stream", json={"text": "/status", "mode": "command"}
        ) as response:
            body = "".join(response.iter_text())
    assert response.status_code == 200
    assert (
        'event: message_done\ndata: {"reply_text":"exec: /status",'
        '"session_key":"fake-sess-1","kind":"system"}'
    ) in body
    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]


def test_chat_stream_offline_is_error_event_and_no_persist(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path, gateway="offline")
    tid = _ticket(db_path)
    with TestClient(app) as client:
        with client.stream(
            "POST", f"/api/chat/{tid}/stream", json={"text": "hello", "mode": "message"}
        ) as response:
            body = "".join(response.iter_text())
    assert response.status_code == 200
    assert 'event: error\ndata: {"code":"gateway_offline","message":"gateway offline"' in body
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


def test_chat_send_second_send_reuses_key_no_new_event(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        first = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
        assert first.json()["session_key"] == "fake-sess-1"
        second = client.post(f"/api/chat/{tid}/send", json={"text": "again"})
    assert second.status_code == 200
    assert second.json() == {"reply_text": "echo: again", "session_key": "fake-sess-1"}
    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]


def test_chat_send_day_materializes_and_persists(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    day = "day_2026-07-04"
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{day}/send", json={"text": "plan check"})
    assert response.status_code == 200
    assert response.json() == {"reply_text": "echo: plan check", "session_key": "fake-sess-1"}
    assert _stored_key(db_path, "days", day) == "fake-sess-1"
    assert _events(db_path, day, "day_created") == [{}]
    assert _events(db_path, day, "chat_session_created") == [{"session_key": "fake-sess-1"}]


def test_chat_send_chief_of_staff_uses_top_level_agent_session(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{CHIEF_OF_STAFF_ENTITY_ID}/send",
            json={"text": "what should I look at?"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "reply_text": "echo: what should I look at?",
        "session_key": "fake-sess-1",
    }
    assert _stored_key(db_path, "agent_chat_sessions", CHIEF_OF_STAFF_ENTITY_ID) == "fake-sess-1"
    assert _events(db_path, CHIEF_OF_STAFF_ENTITY_ID, "chat_session_created") == [
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


def test_chat_send_unknown_agent_entity_not_found(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post("/api/chat/agent_other/send", json={"text": "x"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_chat_send_ticket_not_found(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/chat/t_missing/send", json={"text": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_chat_send_unknown_prefix_not_found(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/chat/xyz/send", json={"text": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_chat_send_offline_is_503_and_no_persist(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path, gateway="offline")
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


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


def test_chat_send_malformed_day_id_rejected_no_materialization(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/chat/day_bogus/send", json={"text": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    conn = connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM days").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    finally:
        conn.close()


def test_chat_send_lost_race_adopts_winner_key_no_event(tmp_path: Path) -> None:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    tid = _ticket(db_path)

    class RacingGateway:
        """First send writes the winning key through its own connection before
        returning a different (losing) key, mimicking a concurrent first send."""

        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def send(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> ChatSendResult:
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
            return ChatSendResult(reply_text="echo: x", session_key="loser-key")

    conn = connect(str(db_path))
    try:
        result = service.send(conn, RacingGateway(), tid, "x", 0)
    finally:
        conn.close()

    assert result.session_key == "winner-key"
    assert _stored_key(db_path, "tickets", tid) == "winner-key"
    assert _events(db_path, tid, "chat_session_created") == []


def test_chat_send_rejects_if_worker_claims_before_first_prompt(tmp_path: Path) -> None:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    tid = _ticket(db_path)

    class WorkerClaimsGateway:
        prompted = False

        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def send(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> ChatSendResult:
            other = connect(str(db_path))
            try:
                other.execute(
                    "UPDATE tickets SET ticket_status = ? WHERE id = ?",
                    (TicketStatus.agent_running_step.value, entity_id),
                )
            finally:
                other.close()
            if on_session_key is not None:
                on_session_key("human-key")
            self.prompted = True
            return ChatSendResult(reply_text="echo: x", session_key="human-key")

    gateway = WorkerClaimsGateway()
    conn = connect(str(db_path))
    try:
        with pytest.raises(PlannerError) as exc_info:
            service.send(conn, gateway, tid, "x", 0)
    finally:
        conn.close()

    assert exc_info.value.code == ErrorCode.already_running
    assert gateway.prompted is False
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


def test_chat_send_stale_session_remints_and_repersists(tmp_path: Path) -> None:
    """A stored key the gateway no longer has: the real adapter mints a fresh session
    (4007 -> create) and returns a new key. The service must replace the stale key and
    log the new one, so the dead key is never resumed again (the 'Gateway Offline' bug)."""
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
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

        def send(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> ChatSendResult:
            assert session_key == "stale-key"  # the stale key is passed through
            if on_session_key is not None:
                on_session_key("fresh-key")
            return ChatSendResult(reply_text="echo: x", session_key="fresh-key")

    conn = connect(str(db_path))
    try:
        result = service.send(conn, ReMintGateway(), tid, "x", 0)
    finally:
        conn.close()

    assert result.session_key == "fresh-key"
    assert _stored_key(db_path, "tickets", tid) == "fresh-key"
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
            response = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
            assert response.status_code == 200
            assert _ticket_status(db_path, tid) == status.value


def test_ticket_chat_send_rejects_while_worker_step_running(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_ticket_status(db_path, tid, TicketStatus.agent_running_step)

    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


def test_ticket_chat_stream_rejects_while_worker_step_running(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_ticket_status(db_path, tid, TicketStatus.agent_running_step)

    with TestClient(app) as client:
        with client.stream(
            "POST", f"/api/chat/{tid}/stream", json={"text": "hello", "mode": "message"}
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert (
        'event: error\ndata: {"code":"already_running",'
        '"message":"ticket worker is already running"'
    ) in body
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


def test_chat_send_busy_is_409_already_running(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    class BusyGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def send(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> ChatSendResult:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id},
            )

    _replace_gateway(app, BusyGateway())
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert _ticket_status(db_path, tid) == TicketStatus.empty.value
