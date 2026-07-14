"""Command catalogue and canonical command-mode Chat turn coverage."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from sqlite3 import Connection

from fastapi.testclient import TestClient

from planner.chat.contracts import (
    GatewayStatus,
    HumanChatCompletion,
    HumanChatObservation,
)
from planner.core.adapters.base import HumanSessionKeyBinder
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
        ticket = create_ticket(
            conn, worker_type="coding", title="Chat me.", actor="human", now=0, title_max_chars=200
        )
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
            "SELECT employee_session_id FROM tickets WHERE id = ?", (entity_id,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row["employee_session_id"]


def _set_ticket_status(db_path: Path, ticket_id: str, status: TicketStatus) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET ticket_status = ? WHERE id = ?", (status.value, ticket_id))
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
            "SELECT status, session_key, error FROM chat_turns "
            "WHERE entity_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return dict(row)


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
        first = client.post(
            f"/api/chat/{tid}/turns",
            json={"text": "/writing-plans", "mode": "command"},
        )
        assert first.status_code == 200
        first_state = _wait_for_settled(client, tid)
        assert first_state["messages"][-1]["role"] == "assistant"
        assert first_state["messages"][-1]["text"] == "skill /writing-plans loaded"
        # a skill run is the first message on the ticket -> mints + logs exactly one event
        assert _stored_key(db_path, tid) == "fake-sess-1"
        assert _events(db_path, tid, "employee_session_changed") == [
            {"employee_session_id": "fake-sess-1"}
        ]
        second = client.post(
            f"/api/chat/{tid}/turns",
            json={"text": "/writing-plans", "mode": "command"},
        )
        assert second.status_code == 200
        _wait_for_settled(client, tid)
    assert _stored_key(db_path, tid) == "fake-sess-1"
    # still exactly one event — the second run does not re-log
    assert _events(db_path, tid, "employee_session_changed") == [
        {"employee_session_id": "fake-sess-1"}
    ]


def test_command_alias_resolves_to_skill(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "/wp", "mode": "command"}
        )
        state = _wait_for_settled(client, tid)
    assert response.status_code == 200
    assert state["messages"][-1]["text"] == "skill /writing-plans loaded"
    assert state["messages"][-1]["role"] == "assistant"


def test_command_display_path_is_system_kind(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "/status", "mode": "command"}
        )
        state = _wait_for_settled(client, tid)
    assert response.status_code == 200
    assert state["messages"][-1]["text"] == "exec: /status"
    assert state["messages"][-1]["role"] == "system"


def test_command_turn_body_validation(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        responses = [
            client.post(f"/api/chat/{tid}/turns", json={}),
            client.post(
                f"/api/chat/{tid}/turns", json={"text": 123, "mode": "command"}
            ),
            client.post(
                f"/api/chat/{tid}/turns", json={"text": "   ", "mode": "command"}
            ),
            client.post(
                f"/api/chat/{tid}/turns", json={"text": "/status", "mode": "invalid"}
            ),
            client.post(
                f"/api/chat/{tid}/turns",
                json={
                    "text": "/status",
                    "mode": "command",
                    "image_references": ["/files/chats/example/image.png"],
                },
            ),
        ]
    assert all(response.status_code == 400 for response in responses)
    assert all(response.json()["error"]["code"] == "validation" for response in responses)


def test_command_rejects_agents(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns",
            json={"text": "/writing-plans", "mode": "command"},
            headers=AGENT,
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_command_not_found_ticket(tmp_path: Path) -> None:
    app, _, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/t_missing/turns",
            json={"text": "/writing-plans", "mode": "command"},
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_command_offline_is_503_and_no_persist(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path, gateway="offline")
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns",
            json={"text": "/writing-plans", "mode": "command"},
        )
        assert response.status_code == 200
        _wait_for_settled(client, tid)
    assert _latest_turn(db_path, tid)["status"] == "errored"
    assert _latest_turn(db_path, tid)["error"] == "gateway offline"
    assert _stored_key(db_path, tid) is None
    assert _events(db_path, tid, "employee_session_changed") == []


def test_command_lost_race_adopts_winner_key_no_event(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)

    class RacingGateway:
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
            yield HumanChatCompletion("skill loaded", "assistant")

    app.state.adapters = Adapters(gateway=RacingGateway())  # type: ignore[arg-type]
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns",
            json={"text": "/writing-plans", "mode": "command"},
        )
        assert response.status_code == 200
        state = _wait_for_settled(client, tid)

    assert state["messages"][-1]["text"] == "skill loaded"
    assert state["messages"][-1]["role"] == "assistant"
    assert _stored_key(db_path, tid) == "winner-key"
    assert _latest_turn(db_path, tid)["session_key"] == "winner-key"
    assert _events(db_path, tid, "employee_session_changed") == []


def test_command_rejects_while_worker_step_running(tmp_path: Path) -> None:
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    _set_ticket_status(db_path, tid, TicketStatus.agent_running_step)

    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "/status", "mode": "command"}
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    assert _stored_key(db_path, tid) is None
    assert _events(db_path, tid, "employee_session_changed") == []


def test_command_compress_surfaces_warning_as_system(tmp_path: Path) -> None:
    # /compress's meaningful feedback rides in slash.exec's `warning`; the real adapter
    # combines output + warning, and the fake models that combined system line. The
    # service must surface both, not just the (empty) output.
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "/compress", "mode": "command"}
        )
        state = _wait_for_settled(client, tid)
    assert response.status_code == 200
    assert state["messages"][-1]["text"] == "(no output)\ncompressed 40 → 8 messages"
    assert state["messages"][-1]["role"] == "system"


def test_command_rotated_key_remints_and_repersists(tmp_path: Path) -> None:
    """/compress rotates the gateway's session key, but slash.exec does not return it —
    it self-heals on the NEXT message, when session.resume follows the continuation chain
    and the adapter returns a key differing from the stored one. The service's re-mint
    branch must replace the stale key and log exactly one employee_session_changed event."""
    app, db_path, _ = _make_app(tmp_path)
    tid = _ticket(db_path)

    seed = connect(str(db_path))  # the pre-compress key, now the stale head of a chain
    try:
        seed.execute("BEGIN IMMEDIATE")
        seed.execute(
            "UPDATE tickets SET employee_session_id = ? WHERE id = ?",
            ("pre-compress", tid),
        )
        seed.execute("COMMIT")
    finally:
        seed.close()

    class RotatedKeyGateway:
        """Resumes the stored key and returns the rotated continuation tip — as the real
        adapter does on the message after a /compress (session.resume follows the chain)."""

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
            assert session_key == "pre-compress"  # the stale key is passed through
            bind_session_key("post-compress")
            yield HumanChatCompletion("exec: /status", "system")

    app.state.adapters = Adapters(gateway=RotatedKeyGateway())  # type: ignore[arg-type]
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "/status", "mode": "command"}
        )
        assert response.status_code == 200
        state = _wait_for_settled(client, tid)

    assert state["messages"][-1]["text"] == "exec: /status"
    assert state["messages"][-1]["role"] == "system"
    assert _stored_key(db_path, tid) == "post-compress"
    assert _events(db_path, tid, "employee_session_changed") == [
        {"employee_session_id": "post-compress"}
    ]


def test_command_busy_is_409_already_running(tmp_path: Path) -> None:
    app, db_path, adapters = _make_app(tmp_path)
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

    app.state.adapters = Adapters(gateway=BusyGateway())  # type: ignore[arg-type]
    with TestClient(app) as client:
        response = client.post(
            f"/api/chat/{tid}/turns", json={"text": "/status", "mode": "command"}
        )
        assert response.status_code == 200
        _wait_for_settled(client, tid)

    assert _latest_turn(db_path, tid)["status"] == "errored"
    assert _latest_turn(db_path, tid)["error"] == "an agent is already running on this ticket"
