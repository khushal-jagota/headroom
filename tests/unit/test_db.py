import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from planner.core import db as db_module
from planner.core.db import BASELINE_REVISION, connect, create_schema

_EMPTY_CODING_FIELDS = json.dumps(
    {
        field: {"value": None, "proposal": None, "user_note": None}
        for field in ("kickoff", "success", "approach", "plan", "implementation", "closeout")
    },
    separators=(",", ":"),
)

# The schema as the hand-written migration ladder left it, captured from the code that
# built it before Alembic replaced the ladder. Databases the ladder built are adopted at
# this shape, and the baseline revision has to keep reproducing exactly it.
SCHEMA_V37_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "schema_v37.sql"


def _schema_objects(conn: sqlite3.Connection) -> dict[str, str]:
    """Every schema object's SQL, with whitespace and IF NOT EXISTS made irrelevant."""
    return {
        str(name): " ".join(str(sql).replace("IF NOT EXISTS ", "").split())
        for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        )
    }


def _table_structure(conn: sqlite3.Connection, table: str) -> dict[str, object]:
    return {
        "columns": [
            (str(c[1]), str(c[2]), int(c[3]), c[4], int(c[5]))
            for c in conn.execute(f"PRAGMA table_info({table})")
        ],
        "foreign_keys": sorted(
            (str(f[2]), str(f[3]), str(f[4]), str(f[6]))
            for f in conn.execute(f"PRAGMA foreign_key_list({table})")
        ),
        "indexes": sorted(
            (str(i[1]), int(i[2]), str(i[3]), int(i[4]))
            for i in conn.execute(f"PRAGMA index_list({table})")
        ),
    }


def _revision(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def _build_pre_alembic_database(path: Path, *, schema_version: int = 37) -> None:
    """A database as the hand-written ladder left it: its schema, its marker, no Alembic."""
    conn = connect(str(path))
    conn.executescript(SCHEMA_V37_FIXTURE.read_text(encoding="utf-8"))
    conn.execute(f"PRAGMA user_version={schema_version}")
    conn.close()


def _insert_ticket(conn: sqlite3.Connection, ticket_id: str, title: str = "Ticket") -> None:
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, fields, "
        "alias, created_at, updated_at) VALUES (?, ?, 'coding', 'hermes', 'needs_success', ?, "
        "?, 1, 1)",
        (ticket_id, title, _EMPTY_CODING_FIELDS, f"alias-{ticket_id}"),
    )


# --- connecting ------------------------------------------------------------------------


def test_connect_applies_busy_timeout_to_initial_connect_and_pragma(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    initial_connect_calls = []

    def recording_connect(*args, **kwargs):
        initial_connect_calls.append((args, kwargs))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(db_module.sqlite3, "connect", recording_connect)

    db_path = str(tmp_path / "bounded-connect.db")
    conn = connect(db_path, busy_timeout_ms=275)
    try:
        assert initial_connect_calls == [((db_path,), {"isolation_level": None, "timeout": 0.275})]
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 275
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


# --- bringing a database up to the current schema ---------------------------------------


def test_fresh_database_is_built_and_marked_at_the_current_revision(tmp_path) -> None:
    conn = connect(str(tmp_path / "fresh.db"))
    create_schema(conn)

    assert _revision(conn) == BASELINE_REVISION
    assert len(_schema_objects(conn)) == 24
    # Carried so a fresh database is not distinguishable from one the old ladder built.
    # An older checkout reads this marker to decide what it still has to do.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 37
    conn.close()


def test_reopening_a_current_database_changes_nothing(tmp_path) -> None:
    """The server calls this on every start; a start must not rewrite the record."""
    conn = connect(str(tmp_path / "reopened.db"))
    create_schema(conn)
    _insert_ticket(conn, "t_survivor", "Still here")
    before = (_schema_objects(conn), _revision(conn))

    create_schema(conn)

    assert (_schema_objects(conn), _revision(conn)) == before
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_survivor'").fetchone()[0] == (
        "Still here"
    )
    conn.close()


def test_database_built_by_the_old_ladder_is_adopted_with_its_rows_intact(tmp_path) -> None:
    db_path = tmp_path / "old.db"
    _build_pre_alembic_database(db_path)
    conn = connect(str(db_path))
    _insert_ticket(conn, "t_old", "Written before Alembic")
    conn.execute(
        "INSERT INTO employee_step_runs (employee_step_id, ticket_id, status, started_at, "
        "updated_at) VALUES ('step_old', 't_old', 'complete', 1, 1)"
    )
    schema_before = _schema_objects(conn)

    create_schema(conn)

    assert _revision(conn) == BASELINE_REVISION
    assert _schema_objects(conn) == schema_before
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_old'").fetchone()[0] == (
        "Written before Alembic"
    )
    assert conn.execute("SELECT count(*) FROM employee_step_runs").fetchone()[0] == 1
    conn.close()


def test_database_older_than_the_baseline_is_refused_by_its_version(tmp_path) -> None:
    db_path = tmp_path / "older.db"
    _build_pre_alembic_database(db_path, schema_version=34)
    conn = connect(str(db_path))

    with pytest.raises(RuntimeError, match="schema version 34"):
        create_schema(conn)

    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
    ).fetchone()
    conn.close()


def test_database_marked_at_the_baseline_but_holding_another_schema_is_refused(tmp_path) -> None:
    conn = connect(str(tmp_path / "mismarked.db"))
    conn.execute("CREATE TABLE something_else (value TEXT)")
    conn.execute("PRAGMA user_version=37")

    with pytest.raises(RuntimeError, match="does not hold that schema"):
        create_schema(conn)

    conn.close()


def test_database_missing_an_index_the_marker_promises_is_refused(tmp_path) -> None:
    """The old ladder wrote its marker before creating indexes, so the two can disagree."""
    db_path = tmp_path / "no-alias-index.db"
    _build_pre_alembic_database(db_path)
    conn = connect(str(db_path))
    conn.execute("DROP INDEX idx_tickets_alias")

    with pytest.raises(RuntimeError, match="idx_tickets_alias"):
        create_schema(conn)

    conn.close()


def test_database_with_an_empty_version_table_is_refused(tmp_path) -> None:
    """Alembic always writes a row, so an empty one says nothing about the schema."""
    conn = connect(str(tmp_path / "blank-version.db"))
    create_schema(conn)
    conn.execute("DELETE FROM alembic_version")

    with pytest.raises(RuntimeError, match="nothing in it"):
        create_schema(conn)

    conn.close()


def test_empty_database_carrying_an_old_marker_is_refused(tmp_path) -> None:
    conn = connect(str(tmp_path / "emptied.db"))
    conn.execute("PRAGMA user_version=34")

    with pytest.raises(RuntimeError, match="no tables but is marked"):
        create_schema(conn)

    conn.close()


def test_the_baseline_revision_still_builds_the_schema_it_was_frozen_at(tmp_path) -> None:
    """The baseline is history: later work adds revisions, it never edits this one."""
    captured = connect(str(tmp_path / "captured.db"))
    captured.executescript(SCHEMA_V37_FIXTURE.read_text(encoding="utf-8"))
    built = connect(str(tmp_path / "built.db"))
    create_schema(built)

    assert _schema_objects(built) == _schema_objects(captured)
    for table in sorted(db_module.PRE_ALEMBIC_TABLE_NAMES):
        assert _table_structure(built, table) == _table_structure(captured, table)
    captured.close()
    built.close()


def test_create_schema_refuses_a_connection_it_cannot_migrate_beside(tmp_path) -> None:
    """Migrations run on their own connection, so the caller's must not hold a lock."""
    conn = connect(str(tmp_path / "busy.db"))
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(RuntimeError, match="no transaction open"):
        create_schema(conn)
    conn.execute("ROLLBACK")
    conn.close()

    in_memory = sqlite3.connect(":memory:")
    with pytest.raises(RuntimeError, match="stored in a file"):
        create_schema(in_memory)
    in_memory.close()


def test_processes_starting_at_once_agree_on_one_database(tmp_path) -> None:
    """Nothing stops two Panels processes bringing the same database up together."""
    db_path = tmp_path / "contended.db"
    # Open once so the file exists in WAL mode. Racing that first open is a question
    # about connect(), not about who gets to build the schema, which is what this asks.
    connect(str(db_path)).close()
    starters = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from planner.core.db import connect, create_schema\n"
                f"conn = connect({str(db_path)!r})\n"
                "create_schema(conn)\n"
                "conn.close()\n",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    failures = [process.communicate()[1] for process in starters if process.wait() != 0]
    assert failures == []

    conn = connect(str(db_path))
    assert [
        str(row[0]) for row in conn.execute("SELECT version_num FROM alembic_version")
    ] == [BASELINE_REVISION]
    assert len(_schema_objects(conn)) == 24
    conn.close()


# --- what the next migration can rely on -------------------------------------------------
#
# These drive real revisions through the real runner. The next package changes a CHECK
# constraint on `tickets`, and every guarantee that work depends on is established here.

_FIXTURE_REVISION_HEADER = '''"""Fixture revision."""

from __future__ import annotations

from alembic import op
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)

revision = "second"
down_revision = "baseline_v37"
branch_labels = None
depends_on = None
'''

# Rebuilding `tickets` the way the next package will: the table declared in full and
# handed over as copy_from, because a rebuild that reflects the old table instead loses
# every CHECK constraint on it.
_REBUILD_TICKETS = (
    _FIXTURE_REVISION_HEADER
    + '''

def tickets_table() -> Table:
    table = Table(
        "tickets",
        MetaData(),
        Column("id", Text, primary_key=True),
        Column("title", Text, nullable=False),
        Column("worker_type", Text, nullable=False),
        Column("employee_backend", Text, nullable=False),
        Column("employee_launch_model", Text),
        Column("employee_launch_reasoning_effort", Text),
        Column("stage", Text, nullable=False, server_default=text("'needs_kickoff'")),
        Column("priority", Text, nullable=False, server_default=text("'P3'")),
        Column("deadline", Text),
        Column("project_id", Text, ForeignKey("projects.id")),
        Column("sprint_item_id", Text, ForeignKey("sprint_items.id")),
        Column("sprint_id", Text, ForeignKey("sprints.id")),
        Column("recap", Text, nullable=False, server_default=text("''")),
        Column("ceiling", Text, nullable=False),
        Column("at_cap", Text, nullable=False, server_default=text("'propose'")),
        Column("ticket_status", Text, nullable=False, server_default=text("'empty'")),
        Column("backend_error", Text),
        Column("stage_ownership_overrides", Text, nullable=False, server_default=text("'{}'")),
        Column("default_stage_ownership_mode", Text),
        Column("employee_session_id", Text),
        Column("alias", Text),
        Column("fields", Text, nullable=False),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')"),
        CheckConstraint("at_cap IN ('stop','propose')"),
        CheckConstraint("ticket_status IN ('empty','settled')"),
        CheckConstraint("default_stage_ownership_mode IN ('worker','user','paired')"),
    )
    # Declared here too, because a rebuild recreates exactly the table it is given and
    # anything left out is dropped — including the unique index on alias.
    Index("idx_tickets_alias", table.c.alias, unique=True, sqlite_where=text("alias IS NOT NULL"))
    Index("idx_tickets_stage", table.c.stage)
    Index("idx_tickets_worker_type_stage", table.c.worker_type, table.c.stage)
    Index("idx_tickets_project_id", table.c.project_id)
    return table



def upgrade() -> None:
    # Rebuild first so the new CHECK is in place, then move the rows onto the new value.
    # The declared CHECK still admits 'empty', so the rows survive the copy.
    with op.batch_alter_table("tickets", copy_from=tickets_table(), recreate="always"):
        pass
    op.execute("UPDATE tickets SET ticket_status = 'settled'")


def downgrade() -> None:
    pass
'''
)

_FAILING_REVISION = (
    _FIXTURE_REVISION_HEADER
    + '''

def upgrade() -> None:
    op.execute("CREATE TABLE half_built (value TEXT)")
    raise RuntimeError("this migration gave up halfway")


def downgrade() -> None:
    pass
'''
)

_ORPHANING_REVISION = (
    _FIXTURE_REVISION_HEADER
    + '''

def upgrade() -> None:
    op.execute("DELETE FROM tickets WHERE id = 't_parent'")


def downgrade() -> None:
    pass
'''
)


def _migration_tree(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the real migrations, for revisions that only exist inside a test."""
    tree = tmp_path / "migrations"
    (tree / "versions").mkdir(parents=True)
    shutil.copy(db_module.MIGRATIONS_DIRECTORY / "env.py", tree / "env.py")
    shutil.copy(
        db_module.MIGRATIONS_DIRECTORY / "versions" / "baseline_v37_schema.py",
        tree / "versions" / "baseline_v37_schema.py",
    )
    monkeypatch.setattr(db_module, "MIGRATIONS_DIRECTORY", tree)
    return tree


def test_rebuilding_a_table_keeps_its_rows_children_checks_and_indexes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _migration_tree(tmp_path, monkeypatch)
    conn = connect(str(tmp_path / "rebuilt.db"))
    create_schema(conn)
    _insert_ticket(conn, "t_parent", "Parent")
    conn.execute(
        "INSERT INTO employee_step_runs (employee_step_id, ticket_id, status, started_at, "
        "updated_at) VALUES ('step_child', 't_parent', 'complete', 1, 1)"
    )
    conn.execute(
        "INSERT INTO ticket_conversation_projections (ticket_id, updated_at) VALUES ('t_parent', 1)"
    )
    structure_before = _table_structure(conn, "tickets")

    (tree / "versions" / "second_revision.py").write_text(_REBUILD_TICKETS, encoding="utf-8")
    create_schema(conn)

    assert _revision(conn) == "second"
    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "ticket_status IN ('empty','settled')" in tickets_sql
    assert "length(title) <= 200" in tickets_sql
    assert "priority IN ('P0','P1','P2','P3')" in tickets_sql
    assert "at_cap IN ('stop','propose')" in tickets_sql
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tickets SET ticket_status = 'retired' WHERE id = 't_parent'")

    # Dropping and recreating the parent left its own row and its children behind, and the
    # rebuilt table kept every index and outgoing foreign key. Those two are asserted
    # directly: a dropped constraint leaves nothing dangling, so no integrity check for
    # the record can notice one going missing.
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_parent'").fetchone()[0] == "Parent"
    assert conn.execute("SELECT count(*) FROM employee_step_runs").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM ticket_conversation_projections").fetchone()[0] == 1
    after = _table_structure(conn, "tickets")
    assert after["indexes"] == structure_before["indexes"]
    assert after["foreign_keys"] == structure_before["foreign_keys"]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_a_migration_that_fails_leaves_the_database_as_it_was(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _migration_tree(tmp_path, monkeypatch)
    conn = connect(str(tmp_path / "failed.db"))
    create_schema(conn)
    _insert_ticket(conn, "t_kept", "Kept")
    before = (_schema_objects(conn), _revision(conn))

    (tree / "versions" / "second_revision.py").write_text(_FAILING_REVISION, encoding="utf-8")
    with pytest.raises(RuntimeError, match="gave up halfway"):
        create_schema(conn)

    assert (_schema_objects(conn), _revision(conn)) == before
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_kept'").fetchone()[0] == "Kept"
    conn.close()


def test_adopting_a_database_is_undone_when_a_later_migration_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live database is adopted once, in the same breath as everything after it."""
    tree = _migration_tree(tmp_path, monkeypatch)
    db_path = tmp_path / "adopted-then-failed.db"
    _build_pre_alembic_database(db_path)
    conn = connect(str(db_path))
    _insert_ticket(conn, "t_old", "Written before Alembic")
    before = _schema_objects(conn)

    (tree / "versions" / "second_revision.py").write_text(_FAILING_REVISION, encoding="utf-8")
    with pytest.raises(RuntimeError, match="gave up halfway"):
        create_schema(conn)

    # No version table, so the database is still the pre-Alembic one it started as.
    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
    ).fetchone()
    assert _schema_objects(conn) == before
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_old'").fetchone()[0] == (
        "Written before Alembic"
    )
    conn.close()


def test_a_migration_that_leaves_a_dangling_reference_is_rolled_back(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Foreign keys are not enforced while migrating, so this is what catches the damage."""
    tree = _migration_tree(tmp_path, monkeypatch)
    conn = connect(str(tmp_path / "orphaned.db"))
    create_schema(conn)
    _insert_ticket(conn, "t_parent", "Parent")
    conn.execute(
        "INSERT INTO employee_step_runs (employee_step_id, ticket_id, status, started_at, "
        "updated_at) VALUES ('step_child', 't_parent', 'complete', 1, 1)"
    )

    (tree / "versions" / "second_revision.py").write_text(_ORPHANING_REVISION, encoding="utf-8")
    with pytest.raises(RuntimeError, match="foreign key violations"):
        create_schema(conn)

    assert _revision(conn) == BASELINE_REVISION
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_parent'").fetchone()[0] == "Parent"
    assert conn.execute("SELECT count(*) FROM employee_step_runs").fetchone()[0] == 1
    conn.close()


# --- the schema the baseline describes ----------------------------------------------------


def test_fresh_schema_drops_enumerating_stage_and_ceiling_checks(tmp_path):
    # t_tt02 retired the two enumerating CHECKs (Stage, ceiling): lifecycle integrity
    # now lives in the registry validation doors, not the DB. The fresh schema no
    # longer rejects a raw out-of-order write at the DB level — the registry is the
    # enforcement (asserted by the door/audit tests). This is the inverse of the old
    # "DB rejects old lifecycle values" behavior and pins the CHECK removal.
    conn = connect(str(tmp_path / "fresh.db"))
    create_schema(conn)
    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "stage IN ('needs_kickoff'" not in tickets_sql
    assert "ceiling IN ('needs_success'" not in tickets_sql
    _insert_ticket(conn, "t_fresh", "Fresh")
    # No enumerating CHECK, so these DB-level writes now succeed (registry gates them).
    conn.execute("UPDATE tickets SET stage = 'in_progress' WHERE id = 't_fresh'")
    conn.execute("UPDATE tickets SET ceiling = 'needs_review' WHERE id = 't_fresh'")
    conn.close()


def test_fresh_schema_rejects_ticket_fields_omission(tmp_path):
    conn = connect(str(tmp_path / "fresh-fields-required.db"))
    create_schema(conn)

    with pytest.raises(sqlite3.IntegrityError, match="tickets.fields"):
        conn.execute(
            "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, "
            "created_at, updated_at) "
            "VALUES ('t_omits_fields', 'Missing fields', 'coding', 'hermes', 'needs_success', 1, 1)"
        )

    conn.close()


def test_fresh_schema_has_no_ticket_execution_route(tmp_path):
    conn = connect(str(tmp_path / "fresh-without-execution-route.db"))
    create_schema(conn)

    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tickets)")}
    assert "execution_route" not in columns
    _insert_ticket(conn, "t_assignment", "A")
    assert conn.execute("SELECT id FROM tickets WHERE id = 't_assignment'").fetchone()[0] == (
        "t_assignment"
    )
    conn.close()


def test_fresh_schema_links_are_blocks_only_without_belongs_to_index(tmp_path):
    conn = connect(str(tmp_path / "fresh-blocks-links.db"))
    create_schema(conn)

    conn.execute(
        "INSERT INTO links (from_id, to_id, kind) VALUES ('t_source', 't_target', 'blocks')"
    )
    for obsolete_kind in ("belongs_to", "parent_child", "relates"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO links (from_id, to_id, kind) VALUES (?, ?, ?)",
                (f"t_{obsolete_kind}", f"t_target_{obsolete_kind}", obsolete_kind),
            )
    indexes = {
        str(row["name"])
        for row in conn.execute("PRAGMA index_list(links)")
        if str(row["origin"]) != "pk"
    }
    assert indexes == {"idx_links_to"}
    conn.close()


def test_fresh_ticket_and_chief_launch_snapshots_are_nullable(tmp_path) -> None:
    conn = connect(str(tmp_path / "fresh-configuration.db"))
    create_schema(conn)
    columns = {str(row["name"]): row for row in conn.execute("PRAGMA table_info(tickets)")}
    assert columns["employee_backend"]["notnull"] == 1
    assert columns["employee_backend"]["dflt_value"] is None
    for name in ("employee_launch_model", "employee_launch_reasoning_effort"):
        assert columns[name]["notnull"] == 0
        assert columns[name]["dflt_value"] is None
    binding_columns = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(conversation_session_bindings)")
    }
    for name in ("employee_launch_model", "employee_launch_reasoning_effort"):
        assert binding_columns[name]["notnull"] == 0
        assert binding_columns[name]["dflt_value"] is None

    with pytest.raises(sqlite3.IntegrityError, match="employee_backend"):
        conn.execute(
            "INSERT INTO tickets (id, title, worker_type, ceiling, fields, created_at, updated_at) "
            "VALUES ('t_missing_backend', 'Missing backend', 'coding', 'needs_kickoff', '{}', 1, 1)"
        )
    with pytest.raises(sqlite3.IntegrityError, match="employee_backend"):
        conn.execute(
            "INSERT INTO tickets "
            "(id, title, worker_type, employee_backend, ceiling, fields, created_at, updated_at) "
            "VALUES ('t_null_backend', 'Null backend', 'coding', NULL, "
            "'needs_kickoff', '{}', 1, 1)"
        )
    conn.close()


def test_fresh_schema_has_worker_type_not_null_no_default_and_composite_index(tmp_path):
    conn = connect(str(tmp_path / "fresh-type.db"))
    create_schema(conn)

    info = {str(row["name"]): row for row in conn.execute("PRAGMA table_info(tickets)")}
    assert info["worker_type"]["notnull"] == 1
    assert info["worker_type"]["dflt_value"] is None
    assert info["ceiling"]["notnull"] == 1
    assert info["ceiling"]["dflt_value"] is None
    assert info["stage"]["notnull"] == 1
    assert info["stage"]["dflt_value"] == "'needs_kickoff'"
    assert info["fields"]["notnull"] == 1
    assert info["fields"]["dflt_value"] is None
    assert info["employee_session_id"]["notnull"] == 0
    assert info["employee_session_id"]["dflt_value"] is None
    assert "execution_route" not in info
    assert info["stage_ownership_overrides"]["notnull"] == 1
    assert info["stage_ownership_overrides"]["dflt_value"] == "'{}'"
    assert info["default_stage_ownership_mode"]["notnull"] == 0
    assert info["default_stage_ownership_mode"]["dflt_value"] is None
    assert "chat_session_key" not in info
    assert "implementer" not in info

    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "worker_type" in tickets_sql
    assert "stage IN ('needs_kickoff'" not in tickets_sql
    assert "ceiling IN ('needs_success'" not in tickets_sql
    assert "length(title) <= 200" in tickets_sql
    assert "priority IN ('P0','P1','P2','P3')" in tickets_sql
    assert "at_cap IN ('stop','propose')" in tickets_sql
    assert "ticket_status IN ('empty'" in tickets_sql
    assert "paired_work" in tickets_sql
    assert "proposal_discussion" in tickets_sql
    assert "execution_route" not in tickets_sql
    assert "khushal" not in tickets_sql

    index_names = {str(row["name"]) for row in conn.execute("PRAGMA index_list(tickets)")}
    assert "idx_tickets_stage" in index_names
    assert "idx_tickets_worker_type_stage" in index_names
    composite_cols = [
        str(row["name"]) for row in conn.execute("PRAGMA index_info(idx_tickets_worker_type_stage)")
    ]
    assert composite_cols == ["worker_type", "stage"]
    conn.close()


def test_create_schema_has_projects_project_ids_and_default_rows(tmp_path):
    conn = connect(str(tmp_path / "fresh.db"))

    create_schema(conn)

    assert {
        row["id"]: (row["name"], row["summary"])
        for row in conn.execute("SELECT id, name, summary FROM projects ORDER BY id")
    } == {
        "project_other": ("Other", ""),
        "project_tribe": ("Tribe", ""),
        "project_vylo": ("Vylo", ""),
    }
    project_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(projects)")}
    assert "summary" in project_columns
    for table in ("sprint_items", "tickets", "ideas"):
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
        assert "project_id" in columns
        assert "project" not in columns
        if table == "sprint_items":
            assert "status" not in columns
            assert "blocked_by" not in columns
            assert "status_proposal" not in columns
    assert {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(pending_worker_context)")
    } == {"worker_entity_id", "context_key", "text", "revision"}
    conn.close()
