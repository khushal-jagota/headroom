"""Creation selects one locale before any canonical write, for every entry path."""

from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock as ClockForTest
from planner.core.contracts import OWNER_PRINCIPAL
from planner.managers import data as sprints
from planner.projects import data as projects
from planner.tickets import data, views
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale


@pytest.mark.parametrize("explicit", [None, "vps", "macos"])
def test_creation_uses_explicit_locale_or_vps(
    tmp_db: Connection,
    explicit: str | None,
) -> None:
    project = projects.create_project(
        tmp_db,
        name="Mac project",
        now=1,
    )
    ticket = data.create_ticket(
        tmp_db,
        title="Locale",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        project_id=project.id,
        locale=explicit,
    )
    expected = explicit or "vps"
    assert ticket.locale.value == expected
    assert views.ticket_json(ticket, 1)["locale"] == expected
    assert data.read_ticket(tmp_db, ticket.id).locale.value == expected
    projects.update_project(tmp_db, project.id, name="Renamed project", now=2)
    assert data.read_ticket(tmp_db, ticket.id).locale.value == expected


def test_inherited_project_keeps_vps_default(tmp_db: Connection, fake_clock: ClockForTest) -> None:
    project = projects.create_project(
        tmp_db,
        name="Inherited project",
        now=1,
    )
    item = sprints.create_manager(
        tmp_db,
        title="Parent",
        project_id=project.id,
        clock=fake_clock,
    )
    context = data.resolve_ticket_creation_context(
        tmp_db,
        title="Inherited",
        worker_type="coding",
        title_max_chars=TITLE_MAX_CHARS,
        manager_id=item.id,
    )
    assert context.project_id == project.id
    assert context.locale is TicketLocale.vps
    ticket = data.create_ticket(
        tmp_db,
        title="Inherited",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        manager_id=item.id,
    )
    assert ticket.locale is TicketLocale.vps


def test_public_http_locale_refusals_override_and_noop(
    tmp_db: Connection, tmp_path: Path, fake_clock: ClockForTest
) -> None:
    """The socket-free HTTP boundary retains the retired locale host's contracts."""
    from fastapi.testclient import TestClient
    from tests.support.in_memory_conversation_system import InMemoryConversationSystem

    from planner.core.config import load_config
    from planner.core.db import connect
    from planner.core.server import create_app

    db_path = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": db_path})
    app = create_app(
        config,
        fake_clock,
        lambda: connect(db_path),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Locale project"})
        assert project.status_code == 200, project.text
        project_id = project.json()["id"]
        assert "requires_macos" not in project.json()
        workers = client.get("/api/workers?detail=summary")
        assert workers.status_code == 200, workers.text
        assert all("requires_macos" not in row for row in workers.json()["workers"])
        worker = client.get("/api/workers/coding")
        assert worker.status_code == 200, worker.text
        assert "requires_macos" not in worker.json()["settings"]
        removed_edit = client.patch(f"/api/projects/{project_id}", json={"requires_macos": True})
        assert removed_edit.status_code == 400, removed_edit.text
        assert (
            client.patch(
                "/api/workers/coding/requirements", json={"requires_macos": True}
            ).status_code
            == 404
        )
        request = {
            "title": "Locale transfer",
            "worker_type": "coding",
            "brief": "Scope.",
            "employee_backend": "claude",
            "employee_launch_model": "scripted-model",
        }
        for choices in ({"locale": "macos"}, {"project_id": project_id}):
            before = [tuple(row) for row in tmp_db.execute("SELECT * FROM tickets ORDER BY id")]
            refusal = client.post(
                "/api/tickets",
                json={**request, **choices, "employee_backend": "unknown-provider"},
            )
            assert refusal.status_code == 400, refusal.text
            assert refusal.json()["error"]["code"] == "validation"
            assert [
                tuple(row) for row in tmp_db.execute("SELECT * FROM tickets ORDER BY id")
            ] == before
        explicit = client.post(
            "/api/tickets", json={**request, "project_id": project_id, "locale": "vps"}
        )
        assert explicit.status_code == 200, explicit.text
        ticket_id = explicit.json()["id"]
        assert explicit.json()["locale"] == "vps"
        assert explicit.json()["project_id"] == project_id
        before_move = dict(
            tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        )
        moved = client.post(f"/api/tickets/{ticket_id}/move", json={"locale": "vps"})
        assert moved.status_code == 404, moved.text
        assert (
            dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
            == before_move
        )
        detail = client.get(f"/api/tickets?detail=full&id={ticket_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["locale"] == "vps"
