"""One way to put a thing in a collection, and one way to take it out.

Every collection keeps the membership rules and refusals it had when each one had
its own pair of routes. These tests state those rules against the single entry point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)})
    return (
        create_app(
            config,
            build_clock(config),
            lambda: connect(str(db_path)),
            conversation_system_for_test=InMemoryConversationSystem(),
        ),
        db_path,
    )


def _member(client: TestClient, collection: str, container: str, member: str) -> str:
    return f"/api/collections/{collection}/{container}/{member}"


def _item(client: TestClient, title: str = "An outcome") -> dict[str, Any]:
    created = client.post("/api/items", json={"title": title, "project_id": "project_vylo"})
    assert created.status_code == 200, created.text
    return cast(dict[str, Any], created.json())


def _sprint(client: TestClient) -> dict[str, Any]:
    created = client.post(
        "/api/sprints",
        json={"name": "A sprint", "date_start": "2026-07-01", "date_end": "2026-07-07"},
    )
    assert created.status_code == 200, created.text
    return cast(dict[str, Any], created.json())


def _ticket(
    client: TestClient,
    title: str = "A ticket",
    item_id: str | None = None,
    worker_type: str = "coding",
) -> str:
    body: dict[str, Any] = {
        "worker_type": worker_type,
        "title": title,
        "kickoff_note": "Start here.",
    }
    if item_id is not None:
        body["sprint_item_id"] = item_id
    created = client.post("/api/tickets", json=body)
    assert created.status_code == 200, created.text
    return str(created.json()["id"])


def _supervisor_headers(item_id: str) -> dict[str, str]:
    return {"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": item_id}


def _worker_headers(ticket_id: str) -> dict[str, str]:
    return {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id}


# --- each collection, added and removed ---------------------------------------


def test_a_ticket_goes_on_a_day_and_comes_off_again(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        ticket_id = _ticket(client)
        path = _member(client, "day_tickets", "2026-07-02", ticket_id)
        added = client.put(path)
        with connect(str(db_path)) as conn:
            on_the_day = conn.execute(
                "SELECT count(*) FROM day_tickets WHERE day_id = ?", ("day_2026-07-02",)
            ).fetchone()[0]
        removed = client.delete(path)

    assert added.status_code == 200, added.text
    assert added.json() == {
        "collection": "day_tickets",
        "container_id": "day_2026-07-02",
        "member_id": ticket_id,
        "ok": True,
    }
    assert on_the_day == 1
    assert removed.status_code == 200, removed.text


def test_a_ticket_goes_under_an_outcome_and_comes_out_again(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _item(client)
        ticket_id = _ticket(client)
        path = _member(client, "outcome_tickets", str(item["id"]), ticket_id)
        added = client.put(path)
        placed = client.get(f"/api/tickets?detail=full&id={ticket_id}").json()["sprint_item_id"]
        removed = client.delete(path)
        detached = client.get(f"/api/tickets?detail=full&id={ticket_id}").json()["sprint_item_id"]

    assert added.status_code == 200, added.text
    assert placed == item["id"]
    assert removed.status_code == 200, removed.text
    assert detached is None


def test_an_outcome_is_committed_to_a_sprint_and_uncommitted(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        sprint = _sprint(client)
        item = _item(client)
        path = _member(client, "sprint_outcomes", str(sprint["id"]), str(item["id"]))
        added = client.put(path)
        with connect(str(db_path)) as conn:
            committed = conn.execute("SELECT count(*) FROM sprint_outcomes").fetchone()[0]
        removed = client.delete(path)
        with connect(str(db_path)) as conn:
            after = conn.execute("SELECT count(*) FROM sprint_outcomes").fetchone()[0]

    assert added.status_code == 200, added.text
    assert committed == 1
    assert removed.status_code == 200, removed.text
    assert after == 0


def test_a_blocker_goes_on_a_ticket_and_comes_off_again(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        blocking = _ticket(client, "Blocker")
        blocked = _ticket(client, "Blocked")
        path = _member(client, "blockers", blocked, blocking)
        added = client.put(path)
        with connect(str(db_path)) as conn:
            blocks = conn.execute("SELECT count(*) FROM ticket_blocks").fetchone()[0]
        removed = client.delete(path)
        with connect(str(db_path)) as conn:
            after = conn.execute("SELECT count(*) FROM ticket_blocks").fetchone()[0]

    assert added.status_code == 200, added.text
    assert blocks == 1
    assert removed.status_code == 200, removed.text
    assert after == 0


# --- the refusals each collection keeps ---------------------------------------


def test_an_unknown_collection_is_a_validation_error(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        response = client.put(_member(client, "favourites", "a", "b"))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_a_planning_sprint_worker_commits_an_outcome_and_another_worker_cannot(
    tmp_path: Path,
) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        sprint = _sprint(client)
        item = _item(client)
        planner_ticket = _ticket(client, "Plan the sprint", worker_type="planning-sprint")
        coding_ticket = _ticket(client, "Ordinary work")
        path = _member(client, "sprint_outcomes", str(sprint["id"]), str(item["id"]))
        refused = client.put(path, headers=_worker_headers(coding_ticket))
        allowed = client.put(path, headers=_worker_headers(planner_ticket))

    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "agent_forbidden"
    assert allowed.status_code == 200, allowed.text


def test_removing_a_ticket_from_an_outcome_it_does_not_belong_to_is_refused(
    tmp_path: Path,
) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        holder = _item(client, "Holder")
        other = _item(client, "Other")
        ticket_id = _ticket(client, item_id=str(holder["id"]))
        response = client.delete(_member(client, "outcome_tickets", str(other["id"]), ticket_id))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


# --- the same call, with a supervisor's authority -----------------------------


def test_a_supervisor_puts_its_own_child_on_a_day_and_no_one_else(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _item(client, "Supervised")
        other = _item(client, "Another")
        child = _ticket(client, "Child", item_id=str(item["id"]))
        outsider = _ticket(client, "Outsider", item_id=str(other["id"]))
        headers = _supervisor_headers(str(item["id"]))
        allowed = client.put(_member(client, "day_tickets", "2026-07-02", child), headers=headers)
        refused = client.put(
            _member(client, "day_tickets", "2026-07-02", outsider), headers=headers
        )
        removed = client.delete(
            _member(client, "day_tickets", "2026-07-02", child), headers=headers
        )

    assert allowed.status_code == 200, allowed.text
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "agent_forbidden"
    assert removed.status_code == 200, removed.text


def test_a_supervisor_blocks_inside_its_own_item_only(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _item(client, "Supervised")
        other = _item(client, "Another")
        blocking = _ticket(client, "Blocker", item_id=str(item["id"]))
        blocked = _ticket(client, "Blocked", item_id=str(item["id"]))
        outsider = _ticket(client, "Outsider", item_id=str(other["id"]))
        headers = _supervisor_headers(str(item["id"]))
        allowed = client.put(_member(client, "blockers", blocked, blocking), headers=headers)
        refused = client.put(_member(client, "blockers", outsider, blocking), headers=headers)

    assert allowed.status_code == 200, allowed.text
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "agent_forbidden"


def test_a_supervisor_cannot_place_a_ticket_under_an_outcome(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _item(client, "Supervised")
        child = _ticket(client, "Child", item_id=str(item["id"]))
        response = client.delete(
            _member(client, "outcome_tickets", str(item["id"]), child),
            headers=_supervisor_headers(str(item["id"])),
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"
