"""Creation selects one locale before any canonical write, for every entry path."""

from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock as ClockForTest
from planner.core.contracts import OWNER_PRINCIPAL, Priority
from planner.core.errors import PlannerError
from planner.projects import data as projects
from planner.sprints import data as sprints
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
        priority=Priority.P2,
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
        priority=Priority.P2,
        requires_macos=True,
        now=1,
    )
    item = sprints.create_item(
        tmp_db,
        title="Parent",
        project_id=project.id,
        priority=Priority.P2,
        clock=fake_clock,
    )
    context = data.resolve_ticket_creation_context(
        tmp_db,
        title="Inherited",
        worker_type="coding",
        title_max_chars=TITLE_MAX_CHARS,
        sprint_item_id=item.id,
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
        sprint_item_id=item.id,
    )
    assert ticket.locale is TicketLocale.macos


@pytest.mark.parametrize("locale", ["macos", "wrong", "", 12])
def test_refused_creation_is_atomic(tmp_db: Connection, locale: object) -> None:
    before = tuple(tmp_db.iterdump())
    with pytest.raises(PlannerError) as error:
        data.create_ticket(
            tmp_db,
            title="Rejected",
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            locale=locale,  # type: ignore[arg-type]
        )
    assert tuple(tmp_db.iterdump()) == before
    if locale == "macos":
        assert error.value.detail["locale"] == "macos"
        assert error.value.detail["employee_backend"]
        assert error.value.detail["reason"]


def test_existing_defaults_and_requirements_round_trip(tmp_db: Connection) -> None:
    assert all(not project.requires_macos for project in projects.list_projects(tmp_db))
    settings = worker_settings.read_worker_settings(
        tmp_db, configured_worker_type_registry(), "coding"
    )
    assert settings.requires_macos is False
    project = projects.create_project(
        tmp_db,
        name="Persisted",
        priority=Priority.P2,
        requires_macos=True,
        now=1,
    )
    assert projects.read_project(tmp_db, project.id).requires_macos is True
    assert projects.project_json(project)["requires_macos"] is True


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
    tree = tmp_path / "predecessor-migrations"
    (tree / "versions").mkdir(parents=True)
    shutil.copy(original / "env.py", tree / "env.py")
    # Select the predecessor chain so later migrations stay outside this old schema.
    for revision in ScriptDirectory(str(original)).walk_revisions(
        base="base", head="disable_retired_worker_schedules"
    ):
        shutil.copy(revision.path, tree / "versions" / Path(revision.path).name)
    conn = db.connect(str(tmp_path / "old.db"))
    monkeypatch.setattr(db, "MIGRATIONS_DIRECTORY", tree)
    db.create_schema(conn)
    conn.execute(
        "INSERT INTO tickets (id,title,worker_type,employee_backend,ceiling,field_values,"
        "worker_step_claim,stage,created_at,updated_at) VALUES "
        "('t_preserved','Existing','coding','hermes','needs_brief','{}','out','needs_brief',1,2)"
    )
    conn.commit()
    before = dict(conn.execute("SELECT * FROM tickets WHERE id='t_preserved'").fetchone())
    projects_before = [dict(row) for row in conn.execute("SELECT * FROM projects")]
    workers_before = [dict(row) for row in conn.execute("SELECT * FROM worker_types")]
    monkeypatch.setattr(db, "MIGRATIONS_DIRECTORY", original)
    db.create_schema(conn)
    after = dict(conn.execute("SELECT * FROM tickets WHERE id='t_preserved'").fetchone())
    assert after.pop("locale") == "vps"
    assert after == before
    for table, rows_before in (("projects", projects_before), ("worker_types", workers_before)):
        rows_after = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        assert all(row.pop("requires_macos") == 0 for row in rows_after)
        assert rows_after == rows_before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


@pytest.mark.parametrize("actor", ["owner", "chief", "sprint_item_supervisor", "worker"])
def test_every_authenticated_creator_uses_locale_resolution(
    tmp_db: Connection,
    tmp_path: Path,
    fake_clock: ClockForTest,
    monkeypatch: pytest.MonkeyPatch,
    actor: str,
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
    project = projects.create_project(
        tmp_db,
        name="API Mac",
        priority=Priority.P2,
        requires_macos=True,
        now=1,
    )
    item = sprints.create_item(
        tmp_db,
        title="API parent",
        project_id=project.id,
        priority=Priority.P2,
        clock=fake_clock,
    )
    db_path = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": db_path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    app = create_app(
        config,
        fake_clock,
        lambda: connect(db_path),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    headers = {} if actor == "owner" else {"X-Plan-Actor": actor}
    if actor == "sprint_item_supervisor":
        headers["X-Plan-Sprint-Item-ID"] = item.id
    elif actor == "worker":
        worker = data.create_ticket(
            tmp_db,
            title="Caller",
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            locale="vps",
            sprint_item_id=item.id,
        )
        headers["X-Plan-Ticket-ID"] = worker.id
    with TestClient(app) as client:
        response = client.post(
            "/api/tickets",
            headers=headers,
            json={
                "title": "Inherited API locale",
                "worker_type": "coding",
                "orchestrator": {"kind": "sprint_item", "id": item.id},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["locale"] == "macos"
        explicit = client.post(
            "/api/tickets",
            headers=headers,
            json={
                "title": "Explicit API locale",
                "worker_type": "coding",
                "locale": "vps",
                "orchestrator": {"kind": "sprint_item", "id": item.id},
            },
        )
        assert explicit.status_code == 200, explicit.text
        assert explicit.json()["locale"] == "vps"
        invalid = client.post(
            "/api/tickets",
            headers=headers,
            json={
                "title": "Invalid API locale",
                "worker_type": "coding",
                "locale": None,
                "orchestrator": {"kind": "sprint_item", "id": item.id},
            },
        )
        assert invalid.status_code == 400, invalid.text


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
