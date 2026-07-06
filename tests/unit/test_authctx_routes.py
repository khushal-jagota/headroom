"""§7.6/§8 route boundary regression: the HTTP PATCH surfaces enforce the same agent
write boundary the CLI does. PATCH /api/tickets/{id} validates a carried claim (a
stale/foreign claim is rejected 409 stale_claim, writing nothing) AND gates human-only
fields; PATCH /api/items/{id} lets an agent transition status only; PATCH
/api/sprints/{id} and PATCH /api/day/{date} are human-only. Human requests behave
exactly as before on every route. Supporting tests, no §18.3 anchor."""

from __future__ import annotations

import time
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from fastapi.testclient import TestClient

from planner.core.adapters.registry import build_adapters
from planner.core.clock import RealClock, build_clock
from planner.core.config import load_config
from planner.core.contracts import Project
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.dispatch import data as dispatch_data
from planner.sprints.data import create_item, create_sprint
from planner.tickets.data import create_ticket

_AGENT = {"X-Plan-Actor": "agent"}  # a plain (non-dispatched) agent context


def _make_app(tmp_path: Path) -> tuple[object, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
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
        ticket = create_ticket(conn, title="Patch me.", actor="human", now=0, title_max_chars=200)
    finally:
        conn.close()
    return ticket.id


def _item(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        item = create_item(conn, title="Item.", project=Project.Vylo, clock=RealClock())
    finally:
        conn.close()
    return item.id


def _sprint(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        sprint = create_sprint(
            conn, name="S1", date_start="2026-07-01", date_end="2026-07-14", clock=RealClock()
        )
    finally:
        conn.close()
    return sprint.id


def _claim(db_path: Path, ticket_id: str) -> dict[str, str]:
    """Grant the ticket an active claim and return the matching claim headers."""
    conn = connect(str(db_path))
    try:
        won = dispatch_data.claim(conn, ticket_id, int(time.time()), 900, pid=1)
    finally:
        conn.close()
    assert won is not None
    run_id, token = won
    return {"X-Plan-Run-Id": run_id, "X-Plan-Claim": token}


def _col(db_path: Path, table: str, id_: str, col: str) -> Any:
    conn = connect(str(db_path))
    try:
        row = conn.execute(f"SELECT {col} FROM {table} WHERE id = ?", (id_,)).fetchone()
    finally:
        conn.close()
    return None if row is None else row[col]


def _priority(db_path: Path, ticket_id: str) -> str:
    return str(_col(db_path, "tickets", ticket_id, "priority"))


# --- PATCH /tickets/{id}: carried-claim gate + human-only fields (§7.6/§8/§14) ----


def test_patch_ticket_with_stale_claim_is_409_and_writes_nothing(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{tid}",
            json={"priority": "P1"},
            headers={"X-Plan-Run-Id": "run_stale", "X-Plan-Claim": "claim_stale"},
        )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "stale_claim"
    assert error["detail"]["reason"] == "none_active"  # no active claim on the ticket
    assert error["detail"]["ticket_id"] == tid
    assert _priority(db_path, tid) == "P3"  # the write never landed


def test_patch_ticket_without_claim_headers_is_unchanged(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        human = client.patch(f"/api/tickets/{tid}", json={"priority": "P1"})
        assert human.status_code == 200
        assert human.json()["priority"] == "P1"
        plain_agent = client.patch(
            f"/api/tickets/{tid}",
            json={"priority": "P2"},
            headers=_AGENT,
        )
    assert plain_agent.status_code == 200
    assert plain_agent.json()["priority"] == "P2"
    assert _priority(db_path, tid) == "P2"


def test_patch_ticket_agent_human_only_field_is_forbidden(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        for field, value in (("title", "Renamed by agent"), ("project", "Vylo")):
            response = client.patch(f"/api/tickets/{tid}", json={field: value}, headers=_AGENT)
            assert response.status_code == 400
            error = response.json()["error"]
            assert error["code"] == "agent_forbidden"
            assert error["detail"]["field"] == field
    # Neither write landed: title/project untouched.
    assert _col(db_path, "tickets", tid, "title") == "Patch me."
    assert _col(db_path, "tickets", tid, "project") is None


def test_patch_ticket_agent_permitted_fields_succeed(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    claim = _claim(db_path, tid)
    with TestClient(app) as client:
        # A claimed agent holding a valid lease may set priority/deadline (the `ticket set`
        # surface); the field gate lets these through and the claim check passes.
        response = client.patch(
            f"/api/tickets/{tid}",
            json={"priority": "P1", "deadline": "2026-08-01"},
            headers={**claim, "X-Plan-Actor": "agent"},
        )
    assert response.status_code == 200, response.json()
    assert response.json()["priority"] == "P1"
    assert _priority(db_path, tid) == "P1"
    assert _col(db_path, "tickets", tid, "deadline") == "2026-08-01"


# --- PATCH /items/{id}: agent may transition status only (§3.2/§8) ---------------


def test_patch_item_agent_plain_field_or_sprint_is_forbidden(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    iid = _item(db_path)
    sid = _sprint(db_path)
    with TestClient(app) as client:
        for body in ({"title": "Agent rename"}, {"priority": "P1"}, {"sprint_id": sid}):
            response = client.patch(f"/api/items/{iid}", json=body, headers=_AGENT)
            assert response.status_code == 400
            error = response.json()["error"]
            assert error["code"] == "agent_forbidden"
            assert error["detail"]["field"] == next(iter(body))
    # Nothing landed: the item is still its original title, priority, and unparented.
    assert _col(db_path, "sprint_items", iid, "title") == "Item."
    assert _col(db_path, "sprint_items", iid, "priority") == "P3"
    assert _col(db_path, "sprint_items", iid, "sprint_id") is None


def test_patch_item_agent_status_transition_succeeds(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    iid = _item(db_path)  # created in `todo`
    with TestClient(app) as client:
        response = client.patch(f"/api/items/{iid}", json={"status": "active"}, headers=_AGENT)
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "active"
    assert _col(db_path, "sprint_items", iid, "status") == "active"


# --- PATCH /sprints/{id}: human-only (§8) ---------------------------------------


def test_patch_sprint_agent_is_forbidden_human_succeeds(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    sid = _sprint(db_path)
    with TestClient(app) as client:
        agent = client.patch(f"/api/sprints/{sid}", json={"name": "Agent edit"}, headers=_AGENT)
        assert agent.status_code == 400
        assert agent.json()["error"]["code"] == "agent_forbidden"
        assert _col(db_path, "sprints", sid, "name") == "S1"  # unchanged
        human = client.patch(f"/api/sprints/{sid}", json={"name": "Human edit"})
    assert human.status_code == 200
    assert human.json()["name"] == "Human edit"
    assert _col(db_path, "sprints", sid, "name") == "Human edit"


def test_patch_sprint_marshals_bad_field_types(tmp_path: Path) -> None:
    # PATCH /sprints marshals each field like the Day PATCH: a non-string value is a
    # clean validation error (400), never a raw SQLite binding error; a null is treated
    # as absent (→ "no fields to update"), never a NOT-NULL crash. Both write nothing.
    # The new Mid-sprint Review field round-trips through the same route.
    app, db_path = _make_app(tmp_path)
    sid = _sprint(db_path)
    with TestClient(app) as client:
        bad = client.patch(f"/api/sprints/{sid}", json={"outcomes": ["nope"]})
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "validation"
        assert _col(db_path, "sprints", sid, "outcomes") == ""  # unchanged, no crash

        nul = client.patch(f"/api/sprints/{sid}", json={"premortem": None})
        assert nul.status_code == 400
        assert nul.json()["error"]["code"] == "validation"
        assert _col(db_path, "sprints", sid, "premortem") == ""  # unchanged, no crash

        ok = client.patch(
            f"/api/sprints/{sid}", json={"mid_where_we_stand": "Halfway, tracking."}
        )
    assert ok.status_code == 200
    assert ok.json()["mid_where_we_stand"] == "Halfway, tracking."
    assert _col(db_path, "sprints", sid, "mid_where_we_stand") == "Halfway, tracking."


# --- PATCH /day/{date}: human-only (§8) -----------------------------------------


def test_patch_day_agent_is_forbidden_human_succeeds(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        agent = client.patch(
            "/api/day/2026-07-05", json={"focus": "Agent focus"}, headers=_AGENT
        )
        assert agent.status_code == 400
        assert agent.json()["error"]["code"] == "agent_forbidden"
        human = client.patch("/api/day/2026-07-05", json={"focus": "Human focus"})
    assert human.status_code == 200
    assert human.json()["focus"] == "Human focus"


# --- POST /sprints/{id}/addenda: human-only (§3.1/§8) ---------------------------


def test_addendum_agent_is_forbidden_human_succeeds(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    sid = _sprint(db_path)
    entry = {"date": "2026-07-05", "text": "Mid-sprint note"}
    with TestClient(app) as client:
        agent = client.post(f"/api/sprints/{sid}/addenda", json=entry, headers=_AGENT)
        assert agent.status_code == 400
        assert agent.json()["error"]["code"] == "agent_forbidden"
        assert _col(db_path, "sprints", sid, "weekly_addenda") == "[]"  # nothing appended
        human = client.post(f"/api/sprints/{sid}/addenda", json=entry)
    assert human.status_code == 200
    assert human.json()["weekly_addenda"] == [entry]


# --- POST /chat/{id}/send: human-only (§8/§11) ----------------------------------


def test_chat_send_agent_is_forbidden_human_succeeds(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)
    entity = "day_2026-07-05"  # a chattable day entity (materializes on read)
    with TestClient(app) as client:
        agent = client.post(
            f"/api/chat/{entity}/send", json={"text": "hi"}, headers=_AGENT
        )
        assert agent.status_code == 400
        assert agent.json()["error"]["code"] == "agent_forbidden"
        human = client.post(f"/api/chat/{entity}/send", json={"text": "hi"})
    assert human.status_code == 200
    assert human.json()["reply_text"] == "echo: hi"  # fake gateway echoes the human's text
