import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import (
    BASELINE_REVISION,
    connect,
    create_schema,
)

_EMPTY_CODING_FIELDS = json.dumps(
    {
        field: {"value": None, "proposal": None, "user_note": None}
        for field in (
            "kickoff",
            "success",
            "approach",
            "plan",
            "implementation",
            "closeout",
        )
    },
    separators=(",", ":"),
)

# The schema as the hand-written migration ladder left it, captured from the code that
# built it before Alembic replaced the ladder. Databases the ladder built are adopted at
# this shape, and the baseline revision has to keep reproducing exactly it.
SCHEMA_V37_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "schema_v37.sql"

# The revision that reshaped ticket statuses, and the current head: a fresh database is
# built to it, and a database the ladder built is adopted at the baseline and brought to it.
RESHAPE_REVISION = "ticket_status_reshape"
HEAD_REVISION = "settled_stage_and_field_ids"

# Later revisions add their durable tables, indexes, and immutability triggers.
CURRENT_SCHEMA_OBJECT_COUNT = 55

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


# What the claim columns add to `tickets`: name, type, NOT NULL, default, primary-key
# position, in PRAGMA table_info's shape. They replaced the stored status and its two
# companions, which this build derives instead.
CLAIM_COLUMN = ("worker_step_claim", "TEXT", 1, "'none'", 0)
CLAIM_CHANGED_AT_COLUMN = ("worker_step_claim_changed_at", "INTEGER", 1, "0", 0)
CLAIM_REVISION_COLUMN = ("worker_step_claim_revision", "INTEGER", 1, "0", 0)
GUIDANCE_COLUMN = ("guidance", "TEXT", 1, "''", 0)


def _table_structure_before_the_claim_columns(
    structure: dict[str, object],
) -> dict[str, object]:
    """The tickets structure before its state-of-control columns were added."""
    columns = list(structure["columns"])  # type: ignore[call-overload]
    assert columns[-3:] == [CLAIM_COLUMN, CLAIM_CHANGED_AT_COLUMN, CLAIM_REVISION_COLUMN]
    columns = columns[:-3]
    holder_column = columns.pop()
    assert holder_column[:3] == ("ceiling_holder", "TEXT", 1)
    assert json.loads(str(holder_column[3]).strip("'")) == {
        "kind": "owner",
        "id": "owner",
    }
    assert columns.pop() == ("pending_proposal", "TEXT", 0, None, 0)
    columns = [
        ("fields", *column[1:]) if column[0] == "field_values" else column for column in columns
    ]
    guidance_column = columns.pop()
    assert guidance_column == GUIDANCE_COLUMN
    sprint_column = columns.pop()
    assert sprint_column[:2] == ("sprint_id", "TEXT")
    item_index = next(i for i, column in enumerate(columns) if column[0] == "sprint_item_id")
    columns.insert(item_index + 1, sprint_column)
    return {**structure, "columns": columns}


def _without_the_stored_status(structure: dict[str, object]) -> dict[str, object]:
    """The old shape with the three columns this build stopped storing removed."""
    retired = {"ticket_status", "ticket_status_changed_at", "ticket_status_revision"}
    return {
        **structure,
        "columns": [
            column
            for column in structure["columns"]  # type: ignore[attr-defined]
            if column[0] not in retired
        ],
    }


def _with_the_conversation_link_renamed(
    structure: dict[str, object],
) -> dict[str, object]:
    """The tickets structure the ladder left, under the one name this build changed.

    A rename is a change of shape, so the adoption promise cannot be "identical". It is
    "the same table, with exactly the column this build renamed renamed" — which is a
    claim a silent second change would still break.
    """
    columns = []
    for name, kind, not_null, default, primary_key in structure["columns"]:  # type: ignore[attr-defined]
        if name == "employee_session_id":
            name = "conversation_id"
        columns.append((name, kind, not_null, default, primary_key))
    return {**structure, "columns": columns}


def _without_ticket_ownership_policy(
    structure: dict[str, object],
) -> dict[str, object]:
    """The current Ticket table after Worker types became the only ownership source."""
    columns = [
        column
        for column in structure["columns"]  # type: ignore[attr-defined]
        if column[0] not in {"stage_ownership_overrides", "default_stage_ownership_mode"}
    ]
    return {**structure, "columns": columns}


def _without_the_cap(structure: dict[str, object]) -> dict[str, object]:
    """The current Ticket table after a ceiling became its whole scope."""
    columns = [
        column
        for column in structure["columns"]  # type: ignore[attr-defined]
        if column[0] != "at_cap"
    ]
    return {**structure, "columns": columns}


def _without_direct_sprint_placement(structure: dict[str, object]) -> dict[str, object]:
    """The adopted Ticket table after the item-only placement migration."""
    columns = [
        column
        for column in structure["columns"]  # type: ignore[attr-defined]
        if column[0] != "sprint_id"
    ]
    foreign_keys = [
        foreign_key
        for foreign_key in structure["foreign_keys"]  # type: ignore[attr-defined]
        if foreign_key[1] != "sprint_id"
    ]
    return {**structure, "columns": columns, "foreign_keys": foreign_keys}


def _without_removed_ticket_fields(structure: dict[str, object]) -> dict[str, object]:
    """The adopted Ticket table after unused payload columns leave the schema."""
    columns = [
        column
        for column in structure["columns"]  # type: ignore[attr-defined]
        if column[0] not in {"alias", "backend_error"}
    ]
    indexes = [
        index
        for index in structure["indexes"]  # type: ignore[attr-defined]
        if index[0] != "idx_tickets_alias"
    ]
    return {**structure, "columns": columns, "indexes": indexes}


def _revision(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def _build_pre_alembic_database(path: Path, *, schema_version: int = 37) -> None:
    """A database as the hand-written ladder left it: its schema, its marker, no Alembic."""
    conn = connect(str(path))
    conn.executescript(SCHEMA_V37_FIXTURE.read_text(encoding="utf-8"))
    conn.execute(f"PRAGMA user_version={schema_version}")
    conn.close()


def _build_database_at_revision(path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(path), 5_000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), revision)
    finally:
        engine.dispose()


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    title: str = "Ticket",
    *,
    ticket_status: str = "empty",
    stage: str = "needs_kickoff",
) -> None:
    ticket_columns = {row[1] for row in conn.execute("PRAGMA table_info(tickets)")}
    legacy = "fields" in ticket_columns
    column = "fields" if legacy else "field_values"
    has_alias = "alias" in ticket_columns
    field_content = json.loads(_EMPTY_CODING_FIELDS)
    if ticket_status == "awaiting_approval":
        field_content["kickoff"]["proposal"] = {
            "body": "",
            "proposed_by": "human",
            "created_at": 1,
        }
    alias_column = "alias, " if has_alias else ""
    alias_value = "?, " if has_alias else ""
    alias_parameters = (f"alias-{ticket_id}",) if has_alias else ()
    # Once the status stopped being stored, the only state of control a fixture can write
    # is the claim. Every status this helper takes that is not a claim is now derived.
    stores_status = "ticket_status" in ticket_columns
    state_column = "ticket_status" if stores_status else "worker_step_claim"
    state_value = (
        ticket_status
        if stores_status
        else {"agent_running_step": "out", "agent": "out", "errored": "errored"}.get(
            ticket_status, "none"
        )
    )
    conn.execute(
        f"INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, {column}, "
        f"{alias_column}{state_column}, stage, created_at, updated_at) "
        f"VALUES (?, ?, 'coding', 'hermes', 'needs_success', ?, {alias_value}?, ?, 1, 1)",
        (
            ticket_id,
            title,
            json.dumps(field_content) if legacy else "{}",
            *alias_parameters,
            state_value,
            stage,
        ),
    )


# --- connecting ------------------------------------------------------------------------


# --- bringing a database up to the current schema ---------------------------------------


def test_ticket_blocks_migration_fails_atomically_for_invalid_legacy_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "invalid-links.db"
    _build_database_at_revision(db_path, "proposal_delivery_failures")
    conn = connect(str(db_path))
    _insert_ticket(conn, "t_blocker")
    _insert_ticket(conn, "t_blocked")
    # The historical CHECK prevented unexpected kinds and self-links. Rebuild the
    # table to prove that the forward migration validates data rather than trusting it.
    conn.execute("ALTER TABLE links RENAME TO _checked_links")
    conn.execute(
        "CREATE TABLE links (from_id TEXT NOT NULL,to_id TEXT NOT NULL,kind TEXT NOT NULL,"
        "PRIMARY KEY(from_id,to_id,kind))"
    )
    conn.execute("DROP TABLE _checked_links")
    legacy_rows = [
        ("t_blocker", "t_blocked", "blocks"),
        ("t_blocker", "t_blocked", "depends_on"),
        ("t_missing", "t_blocked", "blocks"),
        ("t_blocker", "si_not_a_ticket", "blocks"),
        ("t_blocker", "t_blocker", "blocks"),
    ]
    conn.executemany("INSERT INTO links VALUES (?, ?, ?)", legacy_rows)

    with pytest.raises(RuntimeError, match="every legacy row"):
        create_schema(conn)

    assert _revision(conn) == "proposal_delivery_failures"
    remaining_rows = [
        tuple(row) for row in conn.execute("SELECT * FROM links ORDER BY rowid")
    ]
    assert remaining_rows == legacy_rows
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_blocks'"
    ).fetchone() is None
    conn.close()


def test_database_built_by_the_old_ladder_is_adopted_with_its_rows_intact(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "old.db"
    _build_pre_alembic_database(db_path)
    conn = connect(str(db_path))
    _insert_ticket(conn, "t_old", "Written before Alembic", ticket_status="agent_running_step")
    conn.execute(
        "INSERT INTO employee_step_runs (employee_step_id, ticket_id, status, started_at, "
        "updated_at) VALUES ('step_old', 't_old', 'complete', 1, 1)"
    )
    structure_before = _table_structure(conn, "tickets")

    create_schema(conn)

    # Adoption stamps the baseline and the upgrade carries on from there, so the schema
    # afterwards is the current one rather than the one the ladder left. What the adoption
    # promises is that the table keeps its shape and the rows are still there, brought onto
    # the values the revisions since the baseline moved them to.
    assert _revision(conn) == HEAD_REVISION
    assert _table_structure_before_the_claim_columns(
        _table_structure(conn, "tickets")
    ) == _without_the_stored_status(
        _without_the_cap(
            _without_ticket_ownership_policy(
                _without_removed_ticket_fields(
                    _with_the_conversation_link_renamed(structure_before)
                )
            )
        )
    )
    assert len(_schema_objects(conn)) == CURRENT_SCHEMA_OBJECT_COUNT
    assert tuple(
        conn.execute("SELECT title, worker_step_claim FROM tickets WHERE id = 't_old'").fetchone()
    ) == ("Written before Alembic", "out")
    # The step run went with its table. Adoption keeps the rows of everything that
    # survives; a table the conversation layer left behind is not one of those.
    assert "employee_step_runs" not in _schema_objects(conn)
    conn.close()


def test_database_older_than_the_baseline_is_refused_by_its_version(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "older.db"
    _build_pre_alembic_database(db_path, schema_version=34)
    conn = connect(str(db_path))

    with pytest.raises(RuntimeError, match="schema version 34"):
        create_schema(conn)

    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
    ).fetchone()
    conn.close()


def test_database_marked_at_the_baseline_but_holding_another_schema_is_refused(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "mismarked.db"))
    conn.execute("CREATE TABLE something_else (value TEXT)")
    conn.execute("PRAGMA user_version=37")

    with pytest.raises(RuntimeError, match="does not hold that schema"):
        create_schema(conn)

    conn.close()


def test_processes_starting_at_once_agree_on_one_database(tmp_path: Path) -> None:
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
    assert [str(row[0]) for row in conn.execute("SELECT version_num FROM alembic_version")] == [
        HEAD_REVISION
    ]
    assert len(_schema_objects(conn)) == CURRENT_SCHEMA_OBJECT_COUNT
    conn.close()


# --- what a migration can rely on ---------------------------------------------------------
#
# These drive revisions through the real runner, on a migration tree holding the baseline
# and a revision written here. The CHECK-changing rebuild they pin is the one the reshape
# revision now does for real, twice.

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
    + """

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
"""
)

_FAILING_REVISION = (
    _FIXTURE_REVISION_HEADER
    + """

def upgrade() -> None:
    op.execute("CREATE TABLE half_built (value TEXT)")
    raise RuntimeError("this migration gave up halfway")


def downgrade() -> None:
    pass
"""
)

_ORPHANING_REVISION = (
    _FIXTURE_REVISION_HEADER
    + """

def upgrade() -> None:
    op.execute("DELETE FROM tickets WHERE id = 't_parent'")


def downgrade() -> None:
    pass
"""
)


def _migration_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _migration_tree(tmp_path, monkeypatch)
    conn = connect(str(tmp_path / "rebuilt.db"))
    create_schema(conn)
    _insert_ticket(conn, "t_parent", "Parent")
    # The synthetic tree stops at the baseline, so this database is the shape the ladder
    # left — including the two child tables the conversation layer that came after it took
    # away. What is under test is the rebuild, not today's schema.
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


