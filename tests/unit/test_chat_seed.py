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
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    EmployeeSessionHistory,
    EmployeeSessionHistoryMessage,
    TicketStatus,
)
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
        column = "employee_session_id" if table == "tickets" else "chat_session_key"
        row = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (entity_id,)).fetchone()
    finally:
        conn.close()
    return None if row is None else row[column]


def _set_stored_key(db_path: Path, table: str, entity_id: str, key: str) -> None:
    conn = connect(str(db_path))
    try:
        column = "employee_session_id" if table == "tickets" else "chat_session_key"
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (key, entity_id))
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
            "SELECT id, status, session_key, recovery_of_turn_id, error FROM chat_turns "
            "WHERE entity_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return dict(row)


def _row_count(db_path: Path, table: str) -> int:
    conn = connect(str(db_path))
    try:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    finally:
        conn.close()
    return int(row["count"])


def _insert_terminal_chat_turn(
    db_path: Path,
    entity_id: str,
    *,
    turn_id: str,
    origin: str = "human",
    mode: str = "message",
    status: str = "errored",
    output_role: str = "assistant",
    output_text: str = "",
    session_key: str | None,
    error: str | None = "gateway disconnected",
    started_at: int = 10,
) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO chat_turns ("
            "id, entity_id, origin, mode, status, phase, activity_label, output_role, "
            "output_text, session_key, error, started_at, updated_at, completed_at"
            ") VALUES (?, ?, ?, ?, ?, 'settled', NULL, ?, ?, ?, ?, ?, ?, ?)",
            (
                turn_id,
                entity_id,
                origin,
                mode,
                status,
                output_role,
                output_text,
                session_key,
                error,
                started_at,
                started_at + 1,
                started_at + 1,
            ),
        )
    finally:
        conn.close()


def test_employee_session_history_empty_without_session(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    with TestClient(app) as client:
        response = client.get(f"/api/tickets/{tid}/employee-session-history")

    assert response.status_code == 200
    assert response.json() == {"messages": [], "employee_session_id": None}
    assert _stored_key(db_path, "tickets", tid) is None
    assert _events(db_path, tid, "employee_session_changed") == []


def test_employee_session_history_reads_full_fake_trace(tmp_path: Path) -> None:
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
        history = client.get(f"/api/tickets/{tid}/employee-session-history")

    assert history.status_code == 200
    assert history.json() == {
        "messages": [
            {"role": "user", "text": "hello", "created_at": 1},
            {"role": "assistant", "text": "echo: hello", "created_at": 2},
            {"role": "user", "text": "again", "created_at": 3},
            {"role": "assistant", "text": "echo: again", "created_at": 4},
        ],
        "employee_session_id": "fake-sess-1",
    }


def test_panels_state_is_database_only_and_does_not_materialize_empty_entities(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    class NoGatewayCalls:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"state called gateway method {name}")

    _replace_gateway(app, NoGatewayCalls())
    conn = connect(str(db_path))
    try:
        before_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()

    with TestClient(app) as client:
        for entity_id in (
            ticket_id,
            "day_2099-01-01",
            CHIEF_OF_STAFF_ENTITY_ID,
        ):
            response = client.get(f"/api/chat/{entity_id}/state")
            assert response.status_code == 200
            assert response.json() == {"messages": [], "outcomes": [], "active_turn": None}

    conn = connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before_events
        assert conn.execute(
            "SELECT 1 FROM days WHERE id = 'day_2099-01-01'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM agent_chat_sessions WHERE id = ?",
            (CHIEF_OF_STAFF_ENTITY_ID,),
        ).fetchone() is None
    finally:
        conn.close()


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
    assert _events(db_path, tid, "employee_session_changed") == [
        {"employee_session_id": "fake-sess-1"}
    ]
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

        def read_employee_session_history(
            self, employee_session_id: str, ticket_id: str
        ) -> EmployeeSessionHistory:
            return EmployeeSessionHistory(messages=(), employee_session_id=employee_session_id)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
            *,
            require_existing_session: bool = False,
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
        assert active["can_pause"] is True
        assert "session_key" not in active
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

        def read_employee_session_history(
            self, employee_session_id: str, ticket_id: str
        ) -> EmployeeSessionHistory:
            return EmployeeSessionHistory(messages=(), employee_session_id=employee_session_id)

        def run_human_turn(
            self,
            session_key: str | None,
            entity_id: str,
            text: str,
            mode: str,
            bind_session_key: HumanSessionKeyBinder,
            image_paths: tuple[Path, ...] = (),
            *,
            require_existing_session: bool = False,
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

        def read_employee_session_history(
            self, employee_session_id: str, ticket_id: str
        ) -> EmployeeSessionHistory:
            return EmployeeSessionHistory(messages=(), employee_session_id=employee_session_id)

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


def test_employee_session_history_rejects_agents(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    with TestClient(app) as client:
        response = client.get(
            f"/api/tickets/{tid}/employee-session-history",
            headers={"X-Plan-Actor": "agent"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_employee_session_history_offline_is_503_when_session_exists(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path, gateway="offline")
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "stored-key")

    with TestClient(app) as client:
        response = client.get(f"/api/tickets/{tid}/employee-session-history")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"
    assert _stored_key(db_path, "tickets", tid) == "stored-key"


def test_employee_session_history_rotated_id_repersists(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "old-key")

    class RotatingHistoryGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def read_employee_session_history(
            self, employee_session_id: str, ticket_id: str
        ) -> EmployeeSessionHistory:
            assert employee_session_id == "old-key"
            assert ticket_id == tid
            return EmployeeSessionHistory(
                messages=(
                    EmployeeSessionHistoryMessage(
                        role="system", text="worker prompt", created_at=7
                    ),
                ),
                employee_session_id="fresh-key",
            )

    _replace_gateway(app, RotatingHistoryGateway())
    with TestClient(app) as client:
        response = client.get(f"/api/tickets/{tid}/employee-session-history")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [{"role": "system", "text": "worker prompt", "created_at": 7}],
        "employee_session_id": "fresh-key",
    }
    assert _stored_key(db_path, "tickets", tid) == "fresh-key"
    assert _events(db_path, tid, "employee_session_changed") == [
        {"employee_session_id": "fresh-key"}
    ]


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
    assert _events(db_path, tid, "employee_session_changed") == [
        {"employee_session_id": "fake-sess-1"}
    ]


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
    assert _events(db_path, tid, "employee_session_changed") == []


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
    expected_kind = (
        "employee_session_changed" if entity_kind == "ticket" else "chat_session_created"
    )
    expected_payload = (
        {"employee_session_id": "fake-sess-1"}
        if entity_kind == "ticket"
        else {"session_key": "fake-sess-1"}
    )
    assert _events(db_path, entity_id, expected_kind) == [expected_payload]


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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            other = connect(str(db_path))
            try:
                other.execute("BEGIN IMMEDIATE")
                other.execute(
                    "UPDATE tickets SET employee_session_id = ? WHERE id = ?",
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
    assert _events(db_path, tid, "employee_session_changed") == []


def test_human_turn_stale_session_remints_and_repersists(tmp_path: Path) -> None:
    """A stored key the gateway no longer has: the real adapter mints a fresh session
    (4007 -> create) and returns a new key. The service must replace the stale key and
    log the new one, so the dead key is never resumed again (the 'Gateway Offline' bug)."""
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    seed = connect(str(db_path))  # a prior session the gateway has since forgotten
    try:
        seed.execute("BEGIN IMMEDIATE")
        seed.execute("UPDATE tickets SET employee_session_id = ? WHERE id = ?", ("stale-key", tid))
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
            *,
            require_existing_session: bool = False,
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
    assert _events(db_path, tid, "employee_session_changed") == [
        {"employee_session_id": "fresh-key"}
    ]


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
    assert _events(db_path, tid, "employee_session_changed") == []


def test_chat_state_reads_do_not_materialize_empty_day_or_chief_rows(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        day_response = client.get("/api/chat/day_2099-01-01/state")
        chief_response = client.get(f"/api/chat/{CHIEF_OF_STAFF_ENTITY_ID}/state")

    assert day_response.status_code == 200
    assert day_response.json() == {"messages": [], "outcomes": [], "active_turn": None}
    assert chief_response.status_code == 200
    assert chief_response.json() == {"messages": [], "outcomes": [], "active_turn": None}
    assert _row_count(db_path, "days") == 0
    assert _row_count(db_path, "agent_chat_sessions") == 0


def test_terminal_outcome_continuation_eligibility_for_ticket_day_and_chief(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "ticket-session")
    _insert_terminal_chat_turn(
        db_path,
        tid,
        turn_id="run_ticket_failed",
        output_text="ticket partial",
        session_key="ticket-session",
    )
    conn = connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO days (id, chat_session_key, created_at, updated_at) "
            "VALUES ('day_2099-01-02', 'day-session', 1, 1)"
        )
        conn.execute(
            "INSERT INTO agent_chat_sessions (id, chat_session_key, created_at, updated_at) "
            "VALUES (?, 'chief-session', 1, 1)",
            (CHIEF_OF_STAFF_ENTITY_ID,),
        )
    finally:
        conn.close()
    _insert_terminal_chat_turn(
        db_path,
        "day_2099-01-02",
        turn_id="run_day_failed",
        output_text="day partial",
        session_key="day-session",
    )
    _insert_terminal_chat_turn(
        db_path,
        CHIEF_OF_STAFF_ENTITY_ID,
        turn_id="run_chief_failed",
        output_text="chief partial",
        session_key="chief-session",
    )

    with TestClient(app) as client:
        ticket_state = client.get(f"/api/chat/{tid}/state").json()
        day_state = client.get("/api/chat/day_2099-01-02/state").json()
        chief_state = client.get(f"/api/chat/{CHIEF_OF_STAFF_ENTITY_ID}/state").json()

    assert ticket_state["outcomes"][0]["can_continue"] is True
    assert day_state["outcomes"][0]["can_continue"] is True
    assert chief_state["outcomes"][0]["can_continue"] is True


def test_failed_human_turn_projects_partial_output_and_safe_recovery(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    class PartialFailureGateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            assert session_key is None
            assert text == "compare options"
            assert require_existing_session is False
            bind_session_key("recoverable-session")
            yield HumanChatOutputDelta("The safer option is")
            raise PlannerError(ErrorCode.gateway_offline, "gateway disconnected")

    _replace_gateway(app, PartialFailureGateway())
    with TestClient(app) as client:
        started = client.post(
            f"/api/chat/{tid}/turns",
            json={"text": "compare options", "mode": "message"},
        )
        assert started.status_code == 200
        state = _wait_for_settled(client, tid)

    assert [(message["role"], message["text"]) for message in state["messages"]] == [
        ("human", "compare options")
    ]
    assert state["outcomes"] == [
        {
            "turn_id": started.json()["id"],
            "origin": "human",
            "status": "errored",
            "output_role": "assistant",
            "output_text": "The safer option is",
            "error": "gateway disconnected",
            "can_continue": True,
            "completed_at": state["outcomes"][0]["completed_at"],
        }
    ]


def test_continue_failed_turn_reuses_bound_session_without_replaying_prompt(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    calls: list[tuple[str | None, str, bool]] = []

    class RecoveringGateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            calls.append((session_key, text, require_existing_session))
            if len(calls) == 1:
                bind_session_key("recoverable-session")
                yield HumanChatOutputDelta("partial answer")
                raise PlannerError(ErrorCode.gateway_offline, "gateway disconnected")
            assert session_key == "recoverable-session"
            assert require_existing_session is True
            bind_session_key("recoverable-session")
            yield HumanChatCompletion("finished answer", "assistant")

    _replace_gateway(app, RecoveringGateway())
    with TestClient(app) as client:
        started = client.post(
            f"/api/chat/{tid}/turns",
            json={"text": "original uncertain prompt", "mode": "message"},
        )
        first_state = _wait_for_settled(client, tid)
        assert first_state["outcomes"][0]["can_continue"] is True

        continued = client.post(
            f"/api/chat/{tid}/turns/{started.json()['id']}/continue"
        )
        assert continued.status_code == 200, continued.text
        final_state = _wait_for_settled(client, tid)

    assert calls == [
        (None, "original uncertain prompt", False),
        (
            "recoverable-session",
            "Continue the previous response in this existing session.",
            True,
        ),
    ]
    assert [message["text"] for message in final_state["messages"]] == [
        "original uncertain prompt",
        "Continue the previous response in this existing session.",
        "finished answer",
    ]
    assert final_state["outcomes"][0]["output_text"] == "partial answer"
    assert final_state["outcomes"][0]["can_continue"] is False
    latest = _latest_turn(db_path, tid)
    assert latest["recovery_of_turn_id"] == started.json()["id"]
    assert latest["session_key"] == "recoverable-session"


def test_continue_interrupted_turn_reuses_existing_session(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "interrupt-session")
    _insert_terminal_chat_turn(
        db_path,
        tid,
        turn_id="run_interrupted",
        status="interrupted",
        output_text="paused partial",
        session_key="interrupt-session",
        error=None,
    )
    calls: list[tuple[str | None, str, bool]] = []

    class Gateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            calls.append((session_key, text, require_existing_session))
            bind_session_key("interrupt-session")
            yield HumanChatCompletion("continued after pause", "assistant")

    _replace_gateway(app, Gateway())
    with TestClient(app) as client:
        state = client.get(f"/api/chat/{tid}/state").json()
        assert state["outcomes"][0]["status"] == "interrupted"
        assert state["outcomes"][0]["can_continue"] is True
        response = client.post(f"/api/chat/{tid}/turns/run_interrupted/continue")
        assert response.status_code == 200, response.text
        final_state = _wait_for_settled(client, tid)

    assert calls == [
        (
            "interrupt-session",
            "Continue the previous response in this existing session.",
            True,
        )
    ]
    assert final_state["messages"][-1]["text"] == "continued after pause"


@pytest.mark.parametrize(
    ("case", "origin", "session_key", "stored_key", "expected_status", "expected_code"),
    [
        ("unbound", "human", None, "current-session", 400, "validation"),
        ("mismatched", "human", "old-session", "current-session", 400, "validation"),
        ("worker-origin", "worker", "current-session", "current-session", 400, "validation"),
    ],
)
def test_continue_rejects_unsafe_terminal_turns_without_gateway_call(
    tmp_path: Path,
    case: str,
    origin: str,
    session_key: str | None,
    stored_key: str,
    expected_status: int,
    expected_code: str,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, stored_key)
    turn_id = f"run_{case}"
    _insert_terminal_chat_turn(
        db_path,
        tid,
        turn_id=turn_id,
        origin=origin,
        output_text="unsafe partial",
        session_key=session_key,
    )
    calls = 0

    class Gateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            nonlocal calls
            calls += 1
            yield HumanChatCompletion("should not run", "assistant")

    _replace_gateway(app, Gateway())
    with TestClient(app) as client:
        state = client.get(f"/api/chat/{tid}/state").json()
        assert state["outcomes"][0]["can_continue"] is False
        response = client.post(f"/api/chat/{tid}/turns/{turn_id}/continue")

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert calls == 0


def test_continue_rejects_competing_active_turn_without_gateway_call(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "current-session")
    _insert_terminal_chat_turn(
        db_path,
        tid,
        turn_id="run_failed_before_active",
        output_text="partial",
        session_key="current-session",
    )
    conn = connect(str(db_path))
    try:
        chat_data.start_turn(
            conn,
            tid,
            origin="human",
            mode="message",
            visible_role="human",
            visible_text="new active turn",
            output_role="assistant",
            phase="thinking",
            activity_label="Thinking",
            now=20,
        )
    finally:
        conn.close()
    calls = 0

    class Gateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            nonlocal calls
            calls += 1
            yield HumanChatCompletion("should not run", "assistant")

    _replace_gateway(app, Gateway())
    with TestClient(app) as client:
        state = client.get(f"/api/chat/{tid}/state").json()
        assert state["outcomes"][0]["can_continue"] is False
        response = client.post(f"/api/chat/{tid}/turns/run_failed_before_active/continue")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert calls == 0


def test_continue_rechecks_ticket_worker_status_inside_admission(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "current-session")
    _insert_terminal_chat_turn(
        db_path,
        tid,
        turn_id="run_failed_before_worker",
        output_text="partial",
        session_key="current-session",
    )
    _set_ticket_status(db_path, tid, TicketStatus.agent_running_step)
    calls = 0

    conn = connect(str(db_path))
    try:
        with pytest.raises(PlannerError) as excinfo:
            chat_data.start_human_continuation_turn(
                conn,
                tid,
                "run_failed_before_worker",
                entity_kind="ticket",
                visible_text="Continue the previous response in this existing session.",
                now=30,
            )
    finally:
        conn.close()
    assert excinfo.value.code == ErrorCode.already_running

    class Gateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            nonlocal calls
            calls += 1
            yield HumanChatCompletion("should not run", "assistant")

    _replace_gateway(app, Gateway())
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/turns/run_failed_before_worker/continue")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert calls == 0


def test_continue_rejects_duplicate_click_without_second_gateway_call(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_stored_key(db_path, "tickets", tid, "current-session")
    _insert_terminal_chat_turn(
        db_path,
        tid,
        turn_id="run_double_click",
        output_text="partial",
        session_key="current-session",
    )
    calls = 0
    gateway_entered = threading.Event()
    release_gateway = threading.Event()

    class Gateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            nonlocal calls
            calls += 1
            assert session_key == "current-session"
            assert require_existing_session is True
            bind_session_key("current-session")
            gateway_entered.set()
            release_gateway.wait(timeout=5)
            yield HumanChatCompletion("continued once", "assistant")

    _replace_gateway(app, Gateway())
    try:
        with TestClient(app) as client:
            first = client.post(f"/api/chat/{tid}/turns/run_double_click/continue")
            assert first.status_code == 200, first.text
            assert gateway_entered.wait(timeout=5)
            second = client.post(f"/api/chat/{tid}/turns/run_double_click/continue")
            release_gateway.set()
            for _ in range(40):
                final_state = _wait_for_settled(client, tid)
                if any(
                    message["text"] == "continued once"
                    for message in final_state["messages"]
                ):
                    break
                threading.Event().wait(0.05)
            else:
                raise AssertionError(final_state)
    finally:
        release_gateway.set()

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_running"
    assert calls == 1
    assert final_state["messages"][-1]["text"] == "continued once"


def test_ordinary_no_output_failure_projects_without_safe_continuation(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)

    class Gateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            raise PlannerError(ErrorCode.gateway_offline, "gateway disconnected")
            yield HumanChatCompletion("unreachable", "assistant")

    _replace_gateway(app, Gateway())
    with TestClient(app) as client:
        started = client.post(
            f"/api/chat/{tid}/turns", json={"text": "will fail", "mode": "message"}
        )
        state = _wait_for_settled(client, tid)

    assert state["outcomes"] == [
        {
            "turn_id": started.json()["id"],
            "origin": "human",
            "status": "errored",
            "output_role": "assistant",
            "output_text": "",
            "error": "gateway disconnected",
            "can_continue": False,
            "completed_at": state["outcomes"][0]["completed_at"],
        }
    ]


def test_later_human_turn_makes_failed_turn_ineligible_for_continuation(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    calls = 0

    class Gateway:
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
            *,
            require_existing_session: bool = False,
        ) -> Iterator[HumanChatObservation]:
            nonlocal calls
            calls += 1
            bind_session_key("shared-session")
            if calls == 1:
                raise PlannerError(ErrorCode.gateway_offline, "first failed")
            yield HumanChatCompletion("new answer", "assistant")

    _replace_gateway(app, Gateway())
    with TestClient(app) as client:
        failed = client.post(
            f"/api/chat/{tid}/turns", json={"text": "first", "mode": "message"}
        )
        _wait_for_settled(client, tid)
        replacement = client.post(
            f"/api/chat/{tid}/turns", json={"text": "replacement", "mode": "message"}
        )
        assert replacement.status_code == 200
        state = _wait_for_settled(client, tid)
        stale_continue = client.post(
            f"/api/chat/{tid}/turns/{failed.json()['id']}/continue"
        )

    assert state["outcomes"][0]["can_continue"] is False
    assert stale_continue.status_code == 409
    assert stale_continue.json()["error"]["code"] == "already_running"
    assert calls == 2


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
            *,
            require_existing_session: bool = False,
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
