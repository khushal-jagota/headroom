"""Slash-commands + skills (spike 02): the catalog endpoint (shape + TTL cache +
human-only), and running a /command (skill -> assistant, display -> system) with the
same first-reply key-persist + one chat_session_created event as send. Driven through
a TestClient over create_app with the fake (echo) gateway — no real gateway, ever."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from sqlite3 import Connection

from fastapi.testclient import TestClient

from planner.chat import service
from planner.chat.contracts import CommandRunResult, GatewayStatus
from planner.core.adapters.fakes import CANNED_CATALOG, EchoGatewayAdapter
from planner.core.adapters.registry import Adapters, build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.tickets.contracts import TicketStatus
from planner.tickets.data import create_ticket

AGENT = {"X-Plan-Actor": "agent"}


def _make_app(tmp_path: Path, gateway: str = "fake") -> tuple[object, Path, Adapters]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": gateway,
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path, adapters


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


def _stored_key(db_path: Path, entity_id: str) -> object:
    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT chat_session_key FROM tickets WHERE id = ?", (entity_id,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row["chat_session_key"]


def _set_ticket_status(db_path: Path, ticket_id: str, status: TicketStatus) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?", (status.value, ticket_id)
        )
    finally:
        conn.close()


# --- catalog endpoint --------------------------------------------------------


def test_commands_endpoint_returns_catalog_shape(tmp_path: Path) -> None:
    app, _, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/chat/commands")
    assert response.status_code == 200
    # asdict turns the frozen dataclasses into nested dicts; the json round-trip
    # normalizes tuples -> lists so it matches the wire shape the endpoint sends.
    assert response.json() == json.loads(json.dumps(asdict(CANNED_CATALOG)))
    body = response.json()
    # skills are a distinct group, absent from the categories (the load-bearing fact).
    cat_names = [p[0] for cat in body["categories"] for p in cat["pairs"]]
    assert [s[0] for s in body["skills"]] == ["/writing-plans", "/xurl"]
    assert "/writing-plans" not in cat_names


def test_commands_endpoint_is_cached_and_refresh_busts(tmp_path: Path) -> None:
    app, _, adapters = _make_app(tmp_path)
    gateway = adapters.gateway
    assert isinstance(gateway, EchoGatewayAdapter)
    with TestClient(app) as client:
        client.get("/api/chat/commands")
        client.get("/api/chat/commands")
        assert gateway.catalog_calls == 1  # second read served from the TTL cache
        client.get("/api/chat/commands?refresh=1")
    assert gateway.catalog_calls == 2  # ?refresh=1 busted the cache


def test_commands_endpoint_rejects_agents(tmp_path: Path) -> None:
    app, _, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/chat/commands", headers=AGENT)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_commands_endpoint_offline_is_503(tmp_path: Path) -> None:
    app, _, _ = _make_app(tmp_path, gateway="offline")
    with TestClient(app) as client:
        response = client.get("/api/chat/commands")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"


# --- running a /command ------------------------------------------------------


def test_command_skill_path_persists_key_and_one_event(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        first = client.post(f"/api/chat/{tid}/command", json={"command": "/writing-plans"})
        assert first.status_code == 200
        assert first.json() == {
            "reply_text": "skill /writing-plans loaded",
            "session_key": "fake-sess-1",
            "kind": "assistant",
        }
        # a skill run is the first message on the ticket -> mints + logs exactly one event
        assert _stored_key(db_path, tid) == "fake-sess-1"
        assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]
        second = client.post(f"/api/chat/{tid}/command", json={"command": "/writing-plans"})
    assert second.json()["session_key"] == "fake-sess-1"  # reuses the key
    # still exactly one event — the second run does not re-log
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "fake-sess-1"}]


def test_command_alias_resolves_to_skill(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/command", json={"command": "/wp"})
    assert response.status_code == 200
    assert response.json()["reply_text"] == "skill /writing-plans loaded"
    assert response.json()["kind"] == "assistant"


def test_command_display_path_is_system_kind(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/command", json={"command": "/status"})
    assert response.status_code == 200
    assert response.json() == {
        "reply_text": "exec: /status",
        "session_key": "fake-sess-1",
        "kind": "system",
    }


def test_command_requires_nonblank_command(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        missing = client.post(f"/api/chat/{tid}/command", json={})
        blank = client.post(f"/api/chat/{tid}/command", json={"command": "   "})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "validation"
    assert blank.status_code == 400


def test_command_rejects_agents(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/command", json={"command": "/writing-plans"}, headers=AGENT
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_command_not_found_ticket(tmp_path: Path) -> None:
    app, _, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/chat/t_missing/command", json={"command": "/writing-plans"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_command_offline_is_503_and_no_persist(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path, gateway="offline")
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/command", json={"command": "/writing-plans"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"
    assert _stored_key(db_path, tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


def test_command_lost_race_adopts_winner_key_no_event(tmp_path: Path) -> None:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    tid = _ticket(db_path)

    class RacingGateway:
        """A concurrent first reply writes the winning key before run_command returns
        a different (losing) key — the shared first-key persist must adopt the winner."""

        def run_command(
            self,
            session_key: str | None,
            entity_id: str,
            command: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> CommandRunResult:
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
            return CommandRunResult(
                reply_text="skill loaded", session_key="loser-key", kind="assistant"
            )

    conn = connect(str(db_path))
    try:
        result = service.run_command(conn, RacingGateway(), tid, "/writing-plans", 0)  # type: ignore[arg-type]
    finally:
        conn.close()

    assert result.session_key == "winner-key"
    assert result.reply_text == "skill loaded"
    assert _stored_key(db_path, tid) == "winner-key"
    assert _events(db_path, tid, "chat_session_created") == []


def test_command_rejects_while_worker_step_running(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_ticket_status(db_path, tid, TicketStatus.agent_running_step)

    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/command", json={"command": "/status"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert _stored_key(db_path, tid) is None
    assert _events(db_path, tid, "chat_session_created") == []


def test_command_compress_surfaces_warning_as_system(tmp_path: Path) -> None:
    # /compress's meaningful feedback rides in slash.exec's `warning`; the real adapter
    # combines output + warning, and the fake models that combined system line. The
    # service must surface both, not just the (empty) output.
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/command", json={"command": "/compress"})
    assert response.status_code == 200
    assert response.json() == {
        "reply_text": "(no output)\ncompressed 40 → 8 messages",
        "session_key": "fake-sess-1",
        "kind": "system",
    }


def test_command_rotated_key_remints_and_repersists(tmp_path: Path) -> None:
    """/compress rotates the gateway's session key, but slash.exec does not return it —
    it self-heals on the NEXT message, when session.resume follows the continuation chain
    and the adapter returns a key differing from the stored one. The service's re-mint
    branch must replace the stale key and log exactly one chat_session_created (owner #3)."""
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    tid = _ticket(db_path)

    seed = connect(str(db_path))  # the pre-compress key, now the stale head of a chain
    try:
        seed.execute("BEGIN IMMEDIATE")
        seed.execute("UPDATE tickets SET chat_session_key = ? WHERE id = ?", ("pre-compress", tid))
        seed.execute("COMMIT")
    finally:
        seed.close()

    class RotatedKeyGateway:
        """Resumes the stored key and returns the rotated continuation tip — as the real
        adapter does on the message after a /compress (session.resume follows the chain)."""

        def run_command(
            self,
            session_key: str | None,
            entity_id: str,
            command: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> CommandRunResult:
            assert session_key == "pre-compress"  # the stale key is passed through
            if on_session_key is not None:
                on_session_key("post-compress")
            return CommandRunResult(
                reply_text="exec: /status", session_key="post-compress", kind="system"
            )

    conn = connect(str(db_path))
    try:
        result = service.run_command(conn, RotatedKeyGateway(), tid, "/status", 0)  # type: ignore[arg-type]
    finally:
        conn.close()

    assert result.session_key == "post-compress"
    assert result.reply_text == "exec: /status"
    assert result.kind == "system"
    assert _stored_key(db_path, tid) == "post-compress"
    assert _events(db_path, tid, "chat_session_created") == [{"session_key": "post-compress"}]


def test_command_busy_is_409_already_running(tmp_path: Path) -> None:
    app, db_path, adapters = _make_app(tmp_path)
    tid = _ticket(db_path)

    class BusyGateway:
        def status(self) -> GatewayStatus:
            return GatewayStatus(available=True)

        def run_command(
            self,
            session_key: str | None,
            entity_id: str,
            command: str,
            on_session_key: Callable[[str], None] | None = None,
        ) -> CommandRunResult:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id},
            )

    app.state.adapters = Adapters(gateway=BusyGateway())  # type: ignore[arg-type]
    with TestClient(app) as client:
        response = client.post(f"/api/chat/{tid}/command", json={"command": "/status"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
