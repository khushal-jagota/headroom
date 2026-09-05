"""§8 route boundary regression: the HTTP PATCH surfaces enforce the same agent write
boundary the CLI does. PATCH /api/tickets/{id} gates direct-only fields (an agent may
still set the agent-permitted priority/deadline); PATCH /api/items/{id} has no status
write surface because item status is derived; PATCH /api/sprints/{id} and
PATCH /api/day/{date} are direct-only. Unattributed requests behave as before on every
route. The old carried-claim gate is gone (the ticket's code-owned status is the lock
now). Supporting tests, no §18.3 anchor."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import RealClock, build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.sprints.data import create_item, create_sprint
from planner.tickets.contracts import NO_FURTHER, AtCap
from planner.tickets.data import accept_proposal, create_ticket

_AGENT = {"X-Plan-Actor": "agent"}  # an agent context (X-Plan-Actor set)


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, conn_factory), db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            title="Patch me.",
            actor="unattributed",
            now=0,
            title_max_chars=200,
            worker_type="coding",
        )
        ticket = accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="unattributed",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
    finally:
        conn.close()
    return ticket.id


def _worker_headers(db_path: Path, worker_type: str) -> dict[str, str]:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            title=f"{worker_type} fixture",
            actor="unattributed",
            now=0,
            title_max_chars=200,
            worker_type=worker_type,
        )
    finally:
        conn.close()
    return {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket.id}


def _item(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        item = create_item(
            conn, title="Item.", project_id="project_vylo", clock=RealClock()
        )
    finally:
        conn.close()
    return item.id


def _item_in_sprint(db_path: Path, sprint_id: str) -> str:
    conn = connect(str(db_path))
    try:
        item = create_item(
            conn,
            title="Sprint item.",
            project_id="project_vylo",
            sprint_id=sprint_id,
            clock=RealClock(),
        )
    finally:
        conn.close()
    return item.id


def _sprint(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        sprint = create_sprint(
            conn,
            name="S1",
            date_start="2026-07-01",
            date_end="2026-07-14",
            clock=RealClock(),
        )
    finally:
        conn.close()
    return sprint.id


def _col(db_path: Path, table: str, id_: str, col: str) -> Any:
    conn = connect(str(db_path))
    try:
        row = conn.execute(f"SELECT {col} FROM {table} WHERE id = ?", (id_,)).fetchone()
    finally:
        conn.close()
    return None if row is None else row[col]


def _priority(db_path: Path, ticket_id: str) -> str:
    return str(_col(db_path, "tickets", ticket_id, "priority"))


def _ticket_edit_effects(db_path: Path, ticket_id: str) -> tuple[Any, ...]:
    conn = connect(str(db_path))
    try:
        ticket = conn.execute(
            "SELECT title, priority, deadline, project_id, updated_at "
            "FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        assert ticket is not None
        context = conn.execute(
            "SELECT context_key, text, revision FROM pending_worker_context "
            "WHERE worker_entity_id = ? ORDER BY context_key",
            (ticket_id,),
        ).fetchall()
        return (
            tuple(ticket),
            tuple(tuple(row) for row in context),
        )
    finally:
        conn.close()


# --- PATCH /tickets/{id}: direct-only fields + agent-permitted fields (§8/§14) -----


def test_patch_ticket_worker_mixing_permitted_then_forbidden_field_is_atomic(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    before = _ticket_edit_effects(db_path, tid)

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{tid}",
            json={
                "priority": "P1",
                "deadline": "2026-08-01",
                "title": "Forbidden worker title",
            },
            headers=_AGENT,
        )

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "agent_forbidden",
        "message": "direct-only field",
        "detail": {"field": "title", "actor": "agent"},
    }
    assert _ticket_edit_effects(db_path, tid) == before


# --- PATCH /items/{id}: status is derived; fields/sprint remain direct-only --------


def test_planning_sprint_worker_can_shape_item_and_sprint_but_not_delete(
    tmp_path: Path,
    planning_worker_registry: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    headers = _worker_headers(db_path, "planning-sprint")
    wrong_headers = _worker_headers(db_path, "planning-day")
    iid = _item(db_path)
    sid = _sprint(db_path)
    child = _ticket(db_path)
    with TestClient(app) as client:
        item = client.patch(
            f"/api/items/{iid}",
            json={"title": "Planned outcome", "priority": "P1", "sprint_id": sid},
            headers=headers,
        )
        sprint = client.patch(
            f"/api/sprints/{sid}",
            json={"primary_bet": "One clear bet", "date_end": "2026-07-15"},
            headers=headers,
        )
        created_sprint = client.post(
            "/api/sprints",
            json={
                "name": "Next",
                "date_start": "2026-07-16",
                "date_end": "2026-07-29",
            },
            headers=headers,
        )
        created_item = client.post(
            "/api/items",
            json={
                "title": "New planned outcome",
                "priority": "P1",
                "project_id": "project_vylo",
                "sprint_id": sid,
            },
            headers=headers,
        )
        populated = client.post(
            f"/api/items/{iid}/tickets",
            json={"ticket_id": child},
            headers=headers,
        )
        wrong = client.patch(
            f"/api/sprints/{sid}", json={"name": "Wrong worker"}, headers=wrong_headers
        )
        deleted = client.delete(f"/api/items/{iid}", headers=headers)

    assert item.status_code == 200, item.json()
    assert item.json()["title"] == "Planned outcome"
    assert item.json()["sprint_id"] == sid
    assert sprint.status_code == 200, sprint.json()
    assert sprint.json()["primary_bet"] == "One clear bet"
    assert sprint.json()["date_end"] == "2026-07-15"
    assert created_sprint.status_code == 200, created_sprint.json()
    assert created_item.status_code == 200, created_item.json()
    assert created_item.json()["title"] == "New planned outcome"
    assert created_item.json()["sprint_id"] == sid
    assert populated.status_code == 200, populated.json()
    assert wrong.json()["error"]["code"] == "agent_forbidden"
    assert deleted.json()["error"]["code"] == "agent_forbidden"


def test_invalid_compound_item_and_sprint_patches_roll_back_every_field(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    iid = _item(db_path)
    sid = _sprint(db_path)
    with TestClient(app) as client:
        item = client.patch(
            f"/api/items/{iid}",
            json={"title": "Must not land", "sprint_id": "sp_missing"},
        )
        sprint = client.patch(
            f"/api/sprints/{sid}",
            json={"name": "Must not land", "date_end": "not-a-date"},
        )

    assert item.status_code == 404
    assert sprint.status_code == 400
    assert _col(db_path, "sprint_items", iid, "title") == "Item."
    assert _col(db_path, "sprint_items", iid, "sprint_id") is None
    assert _col(db_path, "sprints", sid, "name") == "S1"
    assert _col(db_path, "sprints", sid, "date_end") == "2026-07-14"


def test_item_membership_retries_do_not_detach_a_newer_move(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    sid = _sprint(db_path)
    first = _item_in_sprint(db_path, sid)
    second = _item_in_sprint(db_path, sid)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        for _ in range(2):
            added = client.post(f"/api/items/{first}/tickets", json={"ticket_id": tid})
            assert added.status_code == 200
        moved = client.post(f"/api/items/{second}/tickets", json={"ticket_id": tid})
        stale_remove = client.delete(f"/api/items/{first}/tickets/{tid}")

    assert moved.status_code == 200
    assert stale_remove.status_code == 200
    assert _col(db_path, "tickets", tid, "sprint_item_id") == second


def test_item_placement_changes_notify_child_ticket_workers(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    sid = _sprint(db_path)
    iid = _item_in_sprint(db_path, sid)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        moved = client.post(f"/api/items/{iid}/tickets", json={"ticket_id": tid})
        changed_project = client.patch(
            f"/api/items/{iid}", json={"project_id": "project_other"}
        )

    assert moved.status_code == 200
    assert changed_project.status_code == 200
    _values, context = _ticket_edit_effects(db_path, tid)
    assert context[0][0] == "ticket_changed"
    assert context[0][2] == 2


def test_link_routes_accept_ticket_workers_for_ticket_and_item_targets(
    tmp_path: Path,
    planning_worker_registry: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    source = _ticket(db_path)
    target_ticket = _ticket(db_path)
    target_item = _item(db_path)
    coding_headers = _worker_headers(db_path, "coding")
    planning_headers = _worker_headers(db_path, "planning-sprint")

    with TestClient(app) as client:
        ticket_link = client.post(
            "/api/links",
            json={"from_id": source, "to_id": target_ticket, "kind": "blocks"},
            headers=coding_headers,
        )
        item_link = client.post(
            "/api/links",
            json={"from_id": source, "to_id": target_item, "kind": "blocks"},
            headers=planning_headers,
        )
        remove_ticket_link = client.delete(
            "/api/links",
            params={"from_id": source, "to_id": target_ticket, "kind": "blocks"},
            headers=planning_headers,
        )
        remove_item_link = client.delete(
            "/api/links",
            params={"from_id": source, "to_id": target_item, "kind": "blocks"},
            headers=coding_headers,
        )
        invalid_claim = client.post(
            "/api/links",
            json={"from_id": source, "to_id": target_ticket, "kind": "blocks"},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_missing"},
        )

    assert ticket_link.status_code == 200, ticket_link.json()
    assert item_link.status_code == 200, item_link.json()
    assert remove_ticket_link.status_code == 200, remove_ticket_link.json()
    assert remove_item_link.status_code == 200, remove_item_link.json()
    assert invalid_claim.status_code == 400
    assert invalid_claim.json()["error"]["code"] == "agent_forbidden"

    conn = connect(str(db_path))
    try:
        assert conn.execute("SELECT 1 FROM links").fetchone() is None
    finally:
        conn.close()


# --- PATCH /sprints/{id}: direct-only (§8) --------------------------------------


def test_patch_sprint_agent_is_forbidden_unattributed_succeeds(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    sid = _sprint(db_path)
    with TestClient(app) as client:
        agent = client.patch(
            f"/api/sprints/{sid}", json={"name": "Agent edit"}, headers=_AGENT
        )
        assert agent.status_code == 400
        assert agent.json()["error"]["code"] == "agent_forbidden"
        assert _col(db_path, "sprints", sid, "name") == "S1"  # unchanged
        unattributed = client.patch(f"/api/sprints/{sid}", json={"name": "Direct edit"})
    assert unattributed.status_code == 200
    assert unattributed.json()["name"] == "Direct edit"
    assert _col(db_path, "sprints", sid, "name") == "Direct edit"


# --- PATCH /day/{date}: direct-only (§8) ----------------------------------------


def test_patch_day_agent_is_forbidden_unattributed_succeeds(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        agent = client.patch(
            "/api/day/2026-07-05", json={"focus": "Agent focus"}, headers=_AGENT
        )
        assert agent.status_code == 400
        assert agent.json()["error"]["code"] == "agent_forbidden"
        unattributed = client.patch(
            "/api/day/2026-07-05", json={"focus": "Direct focus"}
        )
    assert unattributed.status_code == 200
    assert unattributed.json()["focus"] == "Direct focus"
