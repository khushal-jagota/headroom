"""Creation selects one locale before any canonical write, for every entry path."""

from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock as ClockForTest
from planner.core.contracts import OWNER_PRINCIPAL
from planner.managers import data as sprints
from planner.projects import data as projects
from planner.tickets import data, locale_configuration, views
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale
from planner.worker_settings import service as worker_settings
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.mark.parametrize("project_requirement", [False, True])
@pytest.mark.parametrize("worker_requirement", [False, True])
@pytest.mark.parametrize("explicit", [None, "vps", "macos"])
def test_creation_uses_explicit_then_either_requirement_then_vps(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
    project_requirement: bool,
    worker_requirement: bool,
    explicit: str | None,
) -> None:
    monkeypatch.setitem(
        locale_configuration.LOCALE_SUPPORTED_BACKENDS,
        TicketLocale.macos,
        locale_configuration.LOCALE_SUPPORTED_BACKENDS[TicketLocale.vps],
    )
    project = projects.create_project(
        tmp_db,
        name="Mac project",
        requires_macos=project_requirement,
        now=1,
    )
    settings = worker_settings.update_worker_requirements(
        tmp_db,
        configured_worker_type_registry(),
        "coding",
        requires_macos=worker_requirement,
        now=1,
    )
    assert settings.requires_macos is worker_requirement
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
    expected = explicit or ("macos" if project_requirement or worker_requirement else "vps")
    assert ticket.locale.value == expected
    assert views.ticket_json(ticket, 1)["locale"] == expected
    assert data.read_ticket(tmp_db, ticket.id).locale.value == expected
    # A requirement is a creation default, never a constraint on later records.
    projects.update_project(tmp_db, project.id, requires_macos=not project_requirement, now=2)
    assert data.read_ticket(tmp_db, ticket.id).locale.value == expected


def test_inherited_project_resolves_before_locale(
    tmp_db: Connection, fake_clock: ClockForTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        locale_configuration.LOCALE_SUPPORTED_BACKENDS,
        TicketLocale.macos,
        locale_configuration.LOCALE_SUPPORTED_BACKENDS[TicketLocale.vps],
    )
    project = projects.create_project(
        tmp_db,
        name="Inherited Mac",
        requires_macos=True,
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
    assert context.locale is TicketLocale.macos
    ticket = data.create_ticket(
        tmp_db,
        title="Inherited",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        manager_id=item.id,
    )
    assert ticket.locale is TicketLocale.macos


def test_worker_requirement_survives_definition_updates(tmp_db: Connection) -> None:
    from planner.worker_types import store

    registry = configured_worker_type_registry()
    worker_settings.update_worker_requirements(
        tmp_db,
        registry,
        "coding",
        requires_macos=True,
        now=1,
    )
    store.write_definition(tmp_db, registry.require("coding"), now=2)
    assert worker_settings.read_worker_requires_macos(tmp_db, "coding") is True


def test_locale_migration_preserves_entire_existing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from alembic.script import ScriptDirectory

    from planner.core import db

    original = db.MIGRATIONS_DIRECTORY
    scripts = ScriptDirectory(str(original))
    locale_revision = scripts.get_revision("ticket_locale")
    assert locale_revision is not None
    predecessor = locale_revision.down_revision
    assert isinstance(predecessor, str)
    tree = tmp_path / "predecessor-migrations"
    (tree / "versions").mkdir(parents=True)
    shutil.copy(original / "env.py", tree / "env.py")
    # Build the actual pre-locale history, regardless of later descendants.
    for revision in scripts.walk_revisions(base="base", head=predecessor):
        path = Path(revision.path)
        shutil.copy(path, tree / "versions" / path.name)
    conn = db.connect(str(tmp_path / "old.db"))
    monkeypatch.setattr(db, "MIGRATIONS_DIRECTORY", tree)
    db.create_schema(conn)
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == predecessor
    assert "locale" not in {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
    conn.execute(
        "INSERT INTO tickets (id,title,worker_type,employee_backend,ceiling,field_values,"
        "worker_step_claim,stage,created_at,updated_at) VALUES "
        "('t_preserved','Existing','coding','hermes','needs_brief','{}','out','needs_brief',1,2)"
    )
    conn.commit()
    before = dict(conn.execute("SELECT * FROM tickets WHERE id='t_preserved'").fetchone())
    projects_before = [dict(row) for row in conn.execute("SELECT * FROM projects")]
    workers_before = [dict(row) for row in conn.execute("SELECT * FROM worker_types")]
    shutil.copy(original / "versions" / "ticket_locale.py", tree / "versions")
    db.create_schema(conn)
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "ticket_locale"
    after = dict(conn.execute("SELECT * FROM tickets WHERE id='t_preserved'").fetchone())
    assert after.pop("locale") == "vps"
    assert after == before
    for table, rows_before in (("projects", projects_before), ("worker_types", workers_before)):
        rows_after = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        assert all(row.pop("requires_macos") == 0 for row in rows_after)
        assert rows_after == rows_before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_move_http_checks_record_precondition_and_authority_before_locale_only_write(
    tmp_db: Connection,
    tmp_path: Path,
    fake_clock: ClockForTest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
    from planner.core.config import load_config
    from planner.core.db import connect
    from planner.core.server import create_app

    monkeypatch.setitem(
        locale_configuration.LOCALE_SUPPORTED_BACKENDS,
        TicketLocale.macos,
        locale_configuration.LOCALE_SUPPORTED_BACKENDS[TicketLocale.vps],
    )
    ticket = data.create_ticket(
        tmp_db,
        title="Move with a record precondition",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    data.set_ticket_backlog(tmp_db, ticket.id, is_backlogged=True, principal=OWNER_PRINCIPAL, now=2)
    stale_hash = data.ticket_record_sha256(tmp_db, ticket.id)
    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"title": "Record changed after the read"},
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    before = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone())
    db_path = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "control.sock"),
        },
    )
    app = create_app(
        config,
        fake_clock,
        lambda: connect(db_path),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:
        stale = client.post(
            f"/api/tickets/{ticket.id}/move",
            json={"locale": "macos", "expected_record_sha256": stale_hash},
        )
        assert stale.status_code == 400, stale.text
        assert "changed" in stale.json()["error"]["message"]
        assert (
            dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone())
            == before
        )
        moved = client.post(
            f"/api/tickets/{ticket.id}/move",
            json={
                "locale": "macos",
                "expected_record_sha256": data.ticket_record_sha256(tmp_db, ticket.id),
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["locale"] == "macos"
        after = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone())
        assert after == {**before, "locale": "macos"}
        refused = client.post(
            f"/api/tickets/{ticket.id}/move",
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket.id},
            json={
                "locale": "vps",
                "expected_record_sha256": data.ticket_record_sha256(tmp_db, ticket.id),
            },
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["code"] == "agent_forbidden"
        assert (
            dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone())
            == after
        )


def test_public_http_locale_refusals_override_noop_and_schedule_preservation(
    tmp_db: Connection, tmp_path: Path, fake_clock: ClockForTest
) -> None:
    """The socket-free HTTP boundary retains the retired locale host's contracts."""
    from fastapi.testclient import TestClient

    from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
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
        project = client.post(
            "/api/projects", json={"name": "Mac requirement", "requires_macos": True}
        )
        assert project.status_code == 200, project.text
        project_id = project.json()["id"]
        request = {
            "title": "Locale transfer",
            "worker_type": "coding",
            "brief": "Scope.",
            "employee_backend": "claude",
            "employee_launch_model": "scripted-unsupported-model",
        }
        for choices in ({"locale": "macos"}, {"project_id": project_id}):
            before = [tuple(row) for row in tmp_db.execute("SELECT * FROM tickets ORDER BY id")]
            refusal = client.post("/api/tickets", json={**request, **choices})
            assert refusal.status_code == 400, refusal.text
            error = refusal.json()["error"]
            assert error["detail"]["locale"] == "macos"
            assert error["detail"]["employee_backend"]
            assert error["detail"]["reason"]
            assert "macos" in error["message"]
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
        assert moved.status_code == 200, moved.text
        assert moved.json()["locale"] == "vps"
        assert (
            dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
            == before_move
        )
        detail = client.get(f"/api/tickets?detail=full&id={ticket_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["locale"] == "vps"
        schedule = client.post(
            "/api/schedules",
            json={
                "title": "Locale schedule",
                "worker_type": "coding",
                "kickoff_note": "Scope.",
                "project_id": project_id,
                "locale": "vps",
                "enabled": False,
                "cadence": "daily",
                "local_time": "23:59",
            },
        )
        assert schedule.status_code == 200, schedule.text
        schedule_id = schedule.json()["id"]
        assert schedule.json()["locale"] == "vps"
        assert client.get(f"/api/schedules/{schedule_id}").json()["locale"] == "vps"
        edited = client.patch(f"/api/schedules/{schedule_id}", json={"title": "Edited title"})
        assert edited.status_code == 200, edited.text
        assert edited.json()["locale"] == "vps"
