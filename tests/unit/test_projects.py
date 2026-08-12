from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

_AGENT = {"X-Plan-Actor": "agent"}


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


def test_project_list_create_duplicate_and_agent_rejection(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        listed = client.get("/api/projects")
        assert listed.status_code == 200
        assert {project["id"]: project["name"] for project in listed.json()["projects"]} == {
            "project_other": "Other",
            "project_personal": "Personal",
            "project_tribe": "Tribe",
            "project_vylo": "Vylo",
        }
        assert all(project["summary"] == "" for project in listed.json()["projects"])
        assert all(project["priority"] is None for project in listed.json()["projects"])

        missing_priority = client.post("/api/projects", json={"name": "Missing Priority"})
        assert missing_priority.status_code == 400
        assert missing_priority.json()["error"]["code"] == "validation"

        invalid_priority = client.post(
            "/api/projects", json={"name": "Invalid Priority", "priority": "urgent"}
        )
        assert invalid_priority.status_code == 400
        assert invalid_priority.json()["error"] == {
            "code": "validation",
            "message": "invalid priority",
            "detail": {"priority": "urgent"},
        }

        created = client.post(
            "/api/projects",
            json={
                "name": "Alpha One",
                "priority": "P1",
                "summary": "  Repo: /srv/alpha  ",
            },
        )
        assert created.status_code == 200, created.json()
        assert created.json()["id"] == "project_alpha_one"
        assert created.json()["name"] == "Alpha One"
        assert created.json()["summary"] == "Repo: /srv/alpha"
        assert created.json()["priority"] == "P1"

        updated = client.patch(
            f"/api/projects/{created.json()['id']}", json={"summary": "  Lives in ~/alpha  "}
        )
        assert updated.status_code == 200, updated.json()
        assert updated.json()["summary"] == "Lives in ~/alpha"

        reassessed = client.patch(
            f"/api/projects/{created.json()['id']}", json={"priority": "P0"}
        )
        assert reassessed.status_code == 200, reassessed.json()
        assert reassessed.json()["priority"] == "P0"
        assert reassessed.json()["name"] == "Alpha One"
        assert reassessed.json()["summary"] == "Lives in ~/alpha"

        invalid_reassessment = client.patch(
            f"/api/projects/{created.json()['id']}", json={"priority": "urgent"}
        )
        assert invalid_reassessment.status_code == 400
        assert invalid_reassessment.json()["error"] == {
            "code": "validation",
            "message": "invalid priority",
            "detail": {"priority": "urgent"},
        }

        cleared = client.patch(f"/api/projects/{created.json()['id']}", json={"summary": ""})
        assert cleared.status_code == 200, cleared.json()
        assert cleared.json()["summary"] == ""

        renamed = client.patch(
            f"/api/projects/{created.json()['id']}", json={"name": "Alpha Renamed"}
        )
        assert renamed.status_code == 200, renamed.json()
        assert renamed.json()["name"] == "Alpha Renamed"

        duplicate = client.post(
            "/api/projects", json={"name": "alpha one", "priority": "P2"}
        )
        assert duplicate.status_code == 200
        duplicate_rename = client.patch(
            f"/api/projects/{renamed.json()['id']}", json={"name": "alpha one"}
        )
        assert duplicate_rename.status_code == 400
        assert duplicate_rename.json()["error"]["code"] == "validation"

        empty_name = client.patch(f"/api/projects/{renamed.json()['id']}", json={"name": " "})
        assert empty_name.status_code == 400
        assert empty_name.json()["error"]["code"] == "validation"

        empty_patch = client.patch(f"/api/projects/{renamed.json()['id']}", json={})
        assert empty_patch.status_code == 400
        assert empty_patch.json()["error"]["code"] == "validation"

        duplicate_create = client.post(
            "/api/projects", json={"name": "alpha one", "priority": "P3"}
        )
        assert duplicate_create.status_code == 400
        assert duplicate_create.json()["error"]["code"] == "validation"

        agent_patch = client.patch(
            f"/api/projects/{renamed.json()['id']}", json={"priority": "P2"}, headers=_AGENT
        )
        assert agent_patch.status_code == 400
        assert agent_patch.json()["error"]["code"] == "agent_forbidden"

        duplicate = duplicate_create
        assert duplicate.json()["error"]["code"] == "validation"

        agent = client.post(
            "/api/projects",
            json={"name": "Agent Project", "priority": "P0"},
            headers=_AGENT,
        )
        assert agent.status_code == 400
        assert agent.json()["error"]["code"] == "agent_forbidden"

    conn = connect(str(db_path))
    try:
        stored = conn.execute(
            "SELECT name, summary, priority FROM projects WHERE id = 'project_alpha_one'"
        ).fetchone()
    finally:
        conn.close()
    assert stored is not None
    assert str(stored["name"]) == "Alpha Renamed"
    assert str(stored["summary"]) == ""
    assert str(stored["priority"]) == "P0"


def test_project_id_and_legacy_project_compatibility(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        project = client.post(
            "/api/projects", json={"name": "Alpha One", "priority": "P2"}
        ).json()

        ticket_by_id = client.post(
            "/api/tickets",
            json={
                "title": "Ticket by id",
                "worker_type": "coding",
                "project_id": project["id"],
            },
        )
        assert ticket_by_id.status_code == 200, ticket_by_id.json()
        assert ticket_by_id.json()["project_id"] == project["id"]
        assert ticket_by_id.json()["project"] == "Alpha One"

        ticket_by_name = client.post(
            "/api/tickets",
            json={
                "title": "Ticket by name",
                "worker_type": "coding",
                "project": "Alpha One",
            },
        )
        assert ticket_by_name.status_code == 200, ticket_by_name.json()
        assert ticket_by_name.json()["project_id"] == project["id"]
        assert ticket_by_name.json()["project"] == "Alpha One"

        listed = client.get(f"/api/tickets?project_id={project['id']}")
        assert listed.status_code == 200, listed.json()
        assert {ticket["id"] for ticket in listed.json()["tickets"]} == {
            ticket_by_id.json()["id"],
            ticket_by_name.json()["id"],
        }

        item = client.post(
            "/api/items",
            json={"title": "Item by id", "project_id": project["id"]},
        )
        assert item.status_code == 200, item.json()
        assert item.json()["project_id"] == project["id"]
        assert item.json()["project"] == "Alpha One"

        idea = client.post(
            "/api/ideas",
            json={"title": "Idea by name", "project": "Alpha One"},
        )
        assert idea.status_code == 200, idea.json()
        assert idea.json()["project_id"] == project["id"]
        assert idea.json()["project"] == "Alpha One"

        other_idea = client.post(
            "/api/ideas",
            json={"title": "Idea by id", "project_id": "project_vylo"},
        )
        assert other_idea.status_code == 200, other_idea.json()

        ideas_by_id = client.get(f"/api/ideas?project_id={project['id']}")
        assert ideas_by_id.status_code == 200, ideas_by_id.json()
        assert {listed_idea["id"] for listed_idea in ideas_by_id.json()["ideas"]} == {
            idea.json()["id"]
        }

        ideas_by_name = client.get("/api/ideas?project=Vylo")
        assert ideas_by_name.status_code == 200, ideas_by_name.json()
        assert {listed_idea["id"] for listed_idea in ideas_by_name.json()["ideas"]} == {
            other_idea.json()["id"]
        }

        mismatch = client.post(
            "/api/items",
            json={"title": "Bad item", "project_id": project["id"], "project": "Vylo"},
        )
        assert mismatch.status_code == 400
        assert mismatch.json()["error"]["code"] == "validation"

        idea_mismatch = client.get(
            "/api/ideas",
            params={"project_id": project["id"], "project": "Vylo"},
        )
        assert idea_mismatch.status_code == 400
        assert idea_mismatch.json()["error"]["code"] == "validation"
