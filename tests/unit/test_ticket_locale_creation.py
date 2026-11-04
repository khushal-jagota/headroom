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


def test_public_http_locale_refusals_override_noop_and_schedule_preservation(
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


def test_requirement_removal_migration_preserves_rows_and_locales(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping legacy defaults must not rewrite unrelated data or stored locales."""
    import shutil

    from alembic.script import ScriptDirectory

    from planner.core import db

    original = db.MIGRATIONS_DIRECTORY
    scripts = ScriptDirectory(str(original))
    removal = scripts.get_revision("remove_macos_requirements")
    assert removal is not None
    predecessor = removal.down_revision
    assert isinstance(predecessor, str)
    tree = tmp_path / "before-removal"
    (tree / "versions").mkdir(parents=True)
    shutil.copy(original / "env.py", tree / "env.py")
    for revision in scripts.walk_revisions(base="base", head=predecessor):
        path = Path(revision.path)
        shutil.copy(path, tree / "versions" / path.name)
    monkeypatch.setattr(db, "MIGRATIONS_DIRECTORY", tree)
    conn = db.connect(str(tmp_path / "legacy.db"))
    try:
        db.create_schema(conn)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == predecessor
        conn.executemany(
            "INSERT INTO projects "
            "(id,name,summary,folder_path,created_at,updated_at,requires_macos) "
            "VALUES (?,?,?,?,1,2,?)",
            (
                ("project_preserved", "Legacy Mac", "Preserve context", "/legacy/project", 1),
                ("project_preserved_vps", "Legacy VPS", "Other context", None, 0),
            ),
        )
        conn.execute("UPDATE worker_types SET requires_macos=1 WHERE worker_type='coding'")
        for locale in ("vps", "macos"):
            conn.execute(
                "INSERT INTO tickets (id,title,worker_type,employee_backend,ceiling,field_values,"
                "stage,project_id,locale,created_at,updated_at) "
                "VALUES (?,?,'coding','codex','needs_brief','{}','needs_brief',"
                "'project_preserved',?,1,2)",
                (f"t_preserved_{locale}", f"Existing {locale}", locale),
            )
            conn.execute(
                "INSERT INTO scheduled_ticket_schedules (id,enabled,cadence,local_time,title,"
                "worker_type,project_id,locale,created_at,updated_at) "
                "VALUES (?,0,'daily','14:30',?,'coding','project_preserved',?,1,2)",
                (f"schedule_preserved_{locale}", f"Existing {locale}", locale),
            )
        conn.commit()
        tables = ("projects", "worker_types", "tickets", "scheduled_ticket_schedules")
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in tables
        }
        for table in ("projects", "worker_types"):
            assert {row.pop("requires_macos") for row in before[table]} == {0, 1}
        metadata_before = list(
            conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE type IN ('index','trigger') ORDER BY type,name"
            )
        )
        shutil.copy(Path(removal.path), tree / "versions")
        db.create_schema(conn)
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == removal.revision
        )
        for table in tables:
            assert [
                dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ] == before[table]
        assert (
            list(
                conn.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master "
                    "WHERE type IN ('index','trigger') ORDER BY type,name"
                )
            )
            == metadata_before
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
