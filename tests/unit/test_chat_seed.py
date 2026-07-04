"""T13 acceptance: the chat send/status routes and the seed route, driven through
a TestClient over create_app with fake adapters. Covers echo persistence + one
event, key reuse, day materialization, not_found/validation/offline error codes,
the guarded first-reply race, and the demo/fixture migration reports."""

from __future__ import annotations

import json
from pathlib import Path
from sqlite3 import Connection

from fastapi.testclient import TestClient

from planner.chat import service
from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.data import create_ticket

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "planning-md"


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


def test_chat_send_echo_persists_key_and_event(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/send", json={"text": "hello"})
    assert response.status_code == 200
    assert response.json() == {"reply_text": "echo: hello", "session_key": "fake-sess-1"}
    assert _stored_key(db_path, "tickets", tid) == "fake-sess-1"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]


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


def test_seed_demo_counts(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/seed", json={"demo": True})
    assert response.status_code == 200
    assert response.json() == {
        "sprints": 1,
        "sprint_items": 3,
        "deferred_items": 0,
        "tickets": 8,
        "ideas": 0,
        "links": 2,
        "duplicates_skipped": 0,
        "skipped": [],
    }


def test_seed_demo_twice_is_db_not_empty(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        first = client.post("/api/seed", json={"demo": True})
        assert first.status_code == 200
        second = client.post("/api/seed", json={"demo": True})
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "db_not_empty"


def test_seed_source_dir_imports_fixture(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/seed", json={"source_dir": str(FIXTURE)})
    assert response.status_code == 200
    body = response.json()
    counts = {
        key: body[key]
        for key in (
            "sprints",
            "sprint_items",
            "deferred_items",
            "tickets",
            "ideas",
            "links",
            "duplicates_skipped",
        )
    }
    assert counts == {
        "sprints": 1,
        "sprint_items": 6,
        "deferred_items": 3,
        "tickets": 4,
        "ideas": 3,
        "links": 1,
        "duplicates_skipped": 0,
    }
    assert len(body["skipped"]) == 6
    assert set(body["skipped"][0]) == {"source_file", "heading", "reason", "excerpt"}


def test_seed_missing_dir_is_validation(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/seed", json={"source_dir": str(tmp_path / "nope")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_seed_file_not_dir_is_validation(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    target = tmp_path / "a.md"
    target.write_text("not a directory")
    with TestClient(app) as client:
        response = client.post("/api/seed", json={"source_dir": str(target)})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_seed_neither_key_is_validation(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/seed", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_seed_both_keys_is_validation(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/seed", json={"source_dir": str(FIXTURE), "demo": True})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


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

        def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
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
