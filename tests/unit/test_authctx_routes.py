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
from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.core.links import add_link
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


def _planning_headers(db_path: Path, worker_type: str) -> dict[str, str]:
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


def test_patch_ticket_unattributed_and_agent_priority_succeed(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        unattributed = client.patch(f"/api/tickets/{tid}", json={"priority": "P1"})
        assert unattributed.status_code == 200
        assert unattributed.json()["priority"] == "P1"
        agent = client.patch(
            f"/api/tickets/{tid}",
            json={"priority": "P2"},
            headers=_AGENT,
        )
    assert agent.status_code == 200
    assert agent.json()["priority"] == "P2"
    assert _priority(db_path, tid) == "P2"


def test_patch_ticket_unattributed_title_and_project_succeed(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        title = client.patch(f"/api/tickets/{tid}", json={"title": "Renamed directly"})
        project = client.patch(f"/api/tickets/{tid}", json={"project": "Vylo"})

    assert title.status_code == 200, title.json()
    assert title.json()["title"] == "Renamed directly"
    assert project.status_code == 200, project.json()
    assert project.json()["project"] == "Vylo"
    assert _col(db_path, "tickets", tid, "title") == "Renamed directly"
    assert _col(db_path, "tickets", tid, "project_id") == "project_vylo"


def test_patch_ticket_agent_direct_only_field_is_forbidden(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        for field, value in (("title", "Renamed by agent"), ("project", "Vylo")):
            response = client.patch(
                f"/api/tickets/{tid}", json={field: value}, headers=_AGENT
            )
            assert response.status_code == 400
            error = response.json()["error"]
            assert error["code"] == "agent_forbidden"
            assert error["detail"]["field"] == field
    # Neither write landed: title/project untouched.
    assert _col(db_path, "tickets", tid, "title") == "Patch me."
    assert _col(db_path, "tickets", tid, "project_id") is None


def test_patch_ticket_agent_permitted_fields_succeed(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        # An agent may set priority/deadline (the `ticket set` surface); the field gate
        # lets these agent-permitted fields through (there is no claim to present now).
        response = client.patch(
            f"/api/tickets/{tid}",
            json={"priority": "P1", "deadline": "2026-08-01"},
            headers=_AGENT,
        )
    assert response.status_code == 200, response.json()
    assert response.json()["priority"] == "P1"
    assert _priority(db_path, tid) == "P1"
    assert _col(db_path, "tickets", tid, "deadline") == "2026-08-01"


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


def test_patch_ticket_worker_can_compound_priority_and_deadline_without_context(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{tid}",
            json={"priority": "P1", "deadline": "2026-08-01"},
            headers=_AGENT,
        )

    assert response.status_code == 200, response.json()
    assert response.json()["priority"] == "P1"
    assert response.json()["deadline"] == "2026-08-01"
    assert _ticket_edit_effects(db_path, tid)[1] == ()


def test_patch_ticket_unattributed_and_chief_keep_ordinary_edit_semantics(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    unattributed_id = _ticket(db_path)
    chief_id = _ticket(db_path)

    with TestClient(app) as client:
        unattributed = client.patch(
            f"/api/tickets/{unattributed_id}",
            json={"priority": "P1", "title": "Unattributed edit"},
        )
        chief = client.patch(
            f"/api/tickets/{chief_id}",
            json={"priority": "P2", "title": "Chief ordinary edit"},
            headers={"X-Plan-Actor": "chief"},
        )

    assert unattributed.status_code == 200, unattributed.json()
    assert chief.status_code == 200, chief.json()
    assert unattributed.json()["title"] == "Unattributed edit"
    assert chief.json()["title"] == "Chief ordinary edit"
    for ticket_id in (unattributed_id, chief_id):
        _values, context = _ticket_edit_effects(db_path, ticket_id)
        assert context[-1][0] == "ticket_changed"
        assert context[-1][2] == 1


def test_patch_ticket_rejects_bad_field_types(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        for body in (
            {"title": 123},
            {"priority": 123},
            {"deadline": 123},
            {"project": 123},
            {"sprint_id": 123},
        ):
            response = client.patch(f"/api/tickets/{tid}", json=body)
            assert response.status_code == 400, body
            assert response.json()["error"]["code"] == "validation"
    assert _col(db_path, "tickets", tid, "title") == "Patch me."
    assert _col(db_path, "tickets", tid, "priority") == "P3"
    assert _col(db_path, "tickets", tid, "deadline") is None
    assert _col(db_path, "tickets", tid, "project_id") is None


# --- PATCH /items/{id}: status is derived; fields/sprint remain direct-only --------


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


def test_planning_sprint_worker_can_shape_item_and_sprint_but_not_delete(
    tmp_path: Path,
    planning_worker_registry: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    headers = _planning_headers(db_path, "planning-sprint")
    wrong_headers = _planning_headers(db_path, "planning-day")
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


def test_delete_item_is_direct_only_and_returns_affected_resources(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    sprint_id = _sprint(db_path)
    item_id = _item_in_sprint(db_path, sprint_id)
    blocker_id = _ticket(db_path)
    conn = connect(str(db_path))
    try:
        add_link(conn, blocker_id, item_id, LinkKind.blocks, 1)
    finally:
        conn.close()

    with TestClient(app) as client:
        forbidden = client.delete(f"/api/items/{item_id}", headers=_AGENT)
        assert forbidden.status_code == 400
        assert forbidden.json()["error"]["code"] == "agent_forbidden"
        assert _col(db_path, "sprint_items", item_id, "title") == "Sprint item."

        response = client.delete(f"/api/items/{item_id}")

    assert response.status_code == 200, response.json()
    assert response.json() == {
        "ok": True,
        "sprint_item_id": item_id,
        "title": "Sprint item.",
        "sprint_ids": [sprint_id],
        "linked_entity_ids": [blocker_id],
    }
    assert _col(db_path, "sprint_items", item_id, "title") is None


def test_patch_item_status_write_is_rejected(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    iid = _item(db_path)
    with TestClient(app) as client:
        response = client.patch(f"/api/items/{iid}", json={"status": "in_progress"})
        agent = client.patch(
            f"/api/items/{iid}", json={"status": "in_progress"}, headers=_AGENT
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"
    assert response.json()["error"]["detail"]["field"] == "status"
    assert agent.status_code == 400
    assert agent.json()["error"]["code"] == "validation"


def test_patch_item_rejects_bad_field_types(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    iid = _item(db_path)
    with TestClient(app) as client:
        for body in (
            {"title": 123},
            {"body": 123},
            {"priority": 123},
            {"deadline": 123},
            {"project": 123},
            {"sprint_id": 123},
            {"status": 123},
            {"status": "blocked", "blocked_by": 123},
            {"status": "blocked", "blocked_by": [123]},
        ):
            response = client.patch(f"/api/items/{iid}", json=body)
            assert response.status_code == 400, body
            assert response.json()["error"]["code"] == "validation"
    assert _col(db_path, "sprint_items", iid, "title") == "Item."
    assert _col(db_path, "sprint_items", iid, "body") == ""
    assert _col(db_path, "sprint_items", iid, "priority") == "P3"
    assert _col(db_path, "sprint_items", iid, "deadline") is None
    assert _col(db_path, "sprint_items", iid, "project_id") == "project_vylo"
    assert _col(db_path, "sprint_items", iid, "sprint_id") is None


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


def test_item_ticket_routes_parent_and_unparent_existing_ticket(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    sid = _sprint(db_path)
    iid = _item_in_sprint(db_path, sid)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        added = client.post(f"/api/items/{iid}/tickets", json={"ticket_id": tid})
        assert added.status_code == 200, added.json()
        assert added.json()["rollup"]["needs_success"] == 1
        removed = client.delete(f"/api/items/{iid}/tickets/{tid}")
        assert removed.status_code == 200, removed.json()
        assert removed.json()["rollup"]["needs_success"] == 0

    assert _col(db_path, "tickets", tid, "sprint_item_id") is None
    assert _col(db_path, "tickets", tid, "project_id") == "project_vylo"
    _values, context = _ticket_edit_effects(db_path, tid)
    assert context[0][0] == "ticket_changed"
    assert context[0][2] == 2


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


def test_item_ticket_routes_are_direct_only(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    sid = _sprint(db_path)
    iid = _item_in_sprint(db_path, sid)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        added = client.post(
            f"/api/items/{iid}/tickets", json={"ticket_id": tid}, headers=_AGENT
        )
        removed = client.delete(f"/api/items/{iid}/tickets/{tid}", headers=_AGENT)
    assert added.status_code == 400
    assert added.json()["error"]["code"] == "agent_forbidden"
    assert removed.status_code == 400
    assert removed.json()["error"]["code"] == "agent_forbidden"
    assert _col(db_path, "tickets", tid, "sprint_item_id") is None


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
