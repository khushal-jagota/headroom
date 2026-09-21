import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from planner.core import db as db_module
from planner.core.db import (
    BASELINE_REVISION,
    connect,
    create_schema,
)

# The head the collapsed sixty-revision chain finished at. A database carrying it holds
# the baseline's schema already, so this build adopts it rather than rebuilding it.
PRE_COLLAPSE_HEAD_REVISION = db_module.PRE_COLLAPSE_HEAD_REVISION

# A revision from the middle of the collapsed chain. No build carries it any more.
REVISION_FROM_THE_COLLAPSED_CHAIN = "proposal_delivery_failures"

# Every table, index and trigger the baseline builds.
CURRENT_SCHEMA_OBJECT_COUNT = 55

# The one state-of-control value this build stores, as the CHECK constraint renders it.
FINAL_WORKER_STEP_CLAIM_CHECK = "worker_step_claim IN ('none','out','errored')"

# The names the reshape moved off. None of them survives, in the schema or in the rows.
RETIRED_TICKET_STATUSES = (
    "agent_running_step",
    "paired_work",
    "user_takeover",
    "proposal_discussion",
)


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


def _revision(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def _head_revision() -> str:
    """The revision this build finishes at, which moves as revisions are added."""
    versions = db_module.MIGRATIONS_DIRECTORY / "versions"
    down_revisions = {
        line.split("=", 1)[1].strip().strip("\"'")
        for path in versions.glob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("down_revision = ")
    }
    heads = {
        line.split("=", 1)[1].strip().strip("\"'")
        for path in versions.glob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("revision = ")
    } - down_revisions
    (head,) = heads
    return head


def _build_database_at_the_pre_collapse_head(path: Path) -> sqlite3.Connection:
    """A database as the collapsed chain left it, which is what the live database becomes.

    The schema at that head is the schema the baseline builds, so the way to make one is
    to build the baseline and then say which revision the database came from. The stale
    marker the pre-Alembic ladder wrote is set too, because the live database still has it.
    """
    conn = connect(str(path))
    create_schema(conn)
    conn.execute("DELETE FROM alembic_version")
    conn.execute("INSERT INTO alembic_version VALUES (?)", (PRE_COLLAPSE_HEAD_REVISION,))
    conn.execute("PRAGMA user_version=37")
    return conn


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    title: str = "Ticket",
    *,
    ticket_status: str = "empty",
    stage: str = "needs_kickoff",
) -> None:
    # The status is derived, so the only state of control a fixture can write is the claim.
    claim = {"agent": "out", "errored": "errored"}.get(ticket_status, "none")
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, field_values, "
        "worker_step_claim, stage, created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'hermes', 'needs_success', '{}', ?, ?, 1, 1)",
        (ticket_id, title, claim, stage),
    )


# --- connecting ------------------------------------------------------------------------


def test_database_at_the_pre_collapse_head_is_adopted_with_its_rows_intact(
    tmp_path: Path,
) -> None:
    """The live database, after the build before this one carried it to that head."""
    conn = _build_database_at_the_pre_collapse_head(tmp_path / "at-the-old-head.db")
    _insert_ticket(conn, "t_carried", "Carried over", ticket_status="agent")
    objects_before = _schema_objects(conn)

    create_schema(conn)

    assert _revision(conn) == _head_revision()
    assert _schema_objects(conn) == objects_before
    assert tuple(
        conn.execute(
            "SELECT title, worker_step_claim FROM tickets WHERE id = 't_carried'"
        ).fetchone()
    ) == ("Carried over", "out")
    # Adoption clears the marker, so this database and a database built from nothing by
    # this build are the same database.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()


def test_database_at_a_revision_from_the_collapsed_chain_is_refused(tmp_path: Path) -> None:
    """The refusal a single deploy gets, when the database still needs the deleted steps."""
    db_path = tmp_path / "mid-chain.db"
    conn = _build_database_at_the_pre_collapse_head(db_path)
    conn.execute("DELETE FROM alembic_version")
    conn.execute("INSERT INTO alembic_version VALUES (?)", (REVISION_FROM_THE_COLLAPSED_CHAIN,))
    _insert_ticket(conn, "t_waiting", "Waiting on the earlier build")

    with pytest.raises(RuntimeError) as refusal:
        create_schema(conn)

    # The message has to be enough to act on: where the database is, where this build
    # starts, and which build to run first.
    message = str(refusal.value)
    assert REVISION_FROM_THE_COLLAPSED_CHAIN in message
    assert BASELINE_REVISION in message
    assert PRE_COLLAPSE_HEAD_REVISION in message
    assert "before the migration chain was collapsed" in message
    # A refused database is left exactly as it was.
    assert _revision(conn) == REVISION_FROM_THE_COLLAPSED_CHAIN
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_waiting'").fetchone()[0] == (
        "Waiting on the earlier build"
    )
    conn.close()


def test_database_with_tables_but_no_version_table_is_refused(tmp_path: Path) -> None:
    """What a database the pre-Alembic ladder built now gets. Two hops, not one."""
    conn = connect(str(tmp_path / "untracked.db"))
    conn.execute("CREATE TABLE something_else (value TEXT)")
    conn.execute("PRAGMA user_version=37")

    with pytest.raises(RuntimeError, match="no version table"):
        create_schema(conn)

    conn.close()


def test_database_at_the_pre_collapse_head_without_that_schema_is_refused(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "mismarked.db"))
    conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES (?)", (PRE_COLLAPSE_HEAD_REVISION,))

    with pytest.raises(RuntimeError, match="does not hold that schema"):
        create_schema(conn)

    conn.close()


def test_a_database_this_build_already_carried_opens_again(tmp_path: Path) -> None:
    """Opening twice must not refuse the second time.

    The adoption guard once named the baseline as the only revision a database could
    already stand on. The first revision added on top of the baseline then made every
    start after the first refuse, because the database now held that revision instead.
    """
    conn = connect(str(tmp_path / "opened-twice.db"))
    create_schema(conn)
    first = _revision(conn)

    create_schema(conn)

    assert _revision(conn) == first == _head_revision()
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
        _head_revision()
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
down_revision = "baseline_2026_09"
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
        Column("conversation_id", Text),
        Column("field_values", Text, nullable=False),
        Column("guidance", Text, nullable=False, server_default=text("''")),
        Column("pending_proposal", Text),
        Column("ceiling_holder", Text, nullable=False),
        Column("worker_step_claim", Text, nullable=False, server_default=text("'none'")),
        Column("worker_step_claim_changed_at", Integer, nullable=False, server_default=text("0")),
        Column("worker_step_claim_revision", Integer, nullable=False, server_default=text("0")),
        Column("created_at", Integer, nullable=False),
        Column("updated_at", Integer, nullable=False),
        CheckConstraint("length(title) <= 200"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')"),
        # Narrowed: the rebuild is what puts a new CHECK in place, and the rows move onto
        # a value it still admits.
        CheckConstraint("worker_step_claim IN ('none','out')"),
        CheckConstraint("worker_step_claim_revision >= 0"),
    )
    # Declared here too, because a rebuild recreates exactly the table it is given and
    # anything left out is dropped — including every index on it.
    Index("idx_tickets_stage", table.c.stage)
    Index("idx_tickets_worker_type_stage", table.c.worker_type, table.c.stage)
    Index("idx_tickets_project_id", table.c.project_id)
    return table



def upgrade() -> None:
    # Rebuild first so the new CHECK is in place, then move the rows onto the new value.
    # The declared CHECK still admits 'none', so the rows survive the copy.
    with op.batch_alter_table("tickets", copy_from=tickets_table(), recreate="always"):
        pass
    op.execute("UPDATE tickets SET worker_step_claim = 'out'")


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
        db_module.MIGRATIONS_DIRECTORY / "versions" / "baseline_2026_09_schema.py",
        tree / "versions" / "baseline_2026_09_schema.py",
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
    _insert_ticket(conn, "t_child", "Child")
    conn.execute(
        "INSERT INTO ticket_blocks (blocking_ticket_id, blocked_ticket_id) "
        "VALUES ('t_parent', 't_child')"
    )
    conn.execute(
        "INSERT INTO ticket_revision_feedback (ticket_id, stage, feedback_json, revision, "
        "created_at, updated_at) VALUES ('t_parent', 'needs_success', '{}', 1, 1, 1)"
    )
    structure_before = _table_structure(conn, "tickets")

    (tree / "versions" / "second_revision.py").write_text(_REBUILD_TICKETS, encoding="utf-8")
    create_schema(conn)

    assert _revision(conn) == "second"
    tickets_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "worker_step_claim IN ('none','out')" in tickets_sql
    assert "length(title) <= 200" in tickets_sql
    assert "priority IN ('P0','P1','P2','P3')" in tickets_sql
    assert "worker_step_claim_revision >= 0" in tickets_sql
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tickets SET worker_step_claim = 'errored' WHERE id = 't_parent'")

    # Dropping and recreating the parent left its own row and its children behind, and the
    # rebuilt table kept every index and outgoing foreign key. Those two are asserted
    # directly: a dropped constraint leaves nothing dangling, so no integrity check for
    # the record can notice one going missing.
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_parent'").fetchone()[0] == "Parent"
    assert conn.execute("SELECT count(*) FROM ticket_blocks").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM ticket_revision_feedback").fetchone()[0] == 1
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
    db_path = tmp_path / "adopted-then-failed.db"
    conn = _build_database_at_the_pre_collapse_head(db_path)
    _insert_ticket(conn, "t_carried", "Carried over")
    before = _schema_objects(conn)
    tree = _migration_tree(tmp_path, monkeypatch)

    (tree / "versions" / "second_revision.py").write_text(_FAILING_REVISION, encoding="utf-8")
    with pytest.raises(RuntimeError, match="gave up halfway"):
        create_schema(conn)

    # Still at the head it arrived with, so the stamp went back with the failed migration.
    assert _revision(conn) == PRE_COLLAPSE_HEAD_REVISION
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 37
    assert _schema_objects(conn) == before
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_carried'").fetchone()[0] == (
        "Carried over"
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
        "INSERT INTO ticket_revision_feedback (ticket_id, stage, feedback_json, revision, "
        "created_at, updated_at) VALUES ('t_parent', 'needs_success', '{}', 1, 1, 1)"
    )

    (tree / "versions" / "second_revision.py").write_text(_ORPHANING_REVISION, encoding="utf-8")
    with pytest.raises(RuntimeError, match="foreign key violations"):
        create_schema(conn)

    assert _revision(conn) == BASELINE_REVISION
    assert conn.execute("SELECT title FROM tickets WHERE id = 't_parent'").fetchone()[0] == "Parent"
    assert conn.execute("SELECT count(*) FROM ticket_revision_feedback").fetchone()[0] == 1
    conn.close()


# --- the schema the baseline describes ----------------------------------------------------


_NOTIFICATION_TABLES = (
    "notification_preferences",
    "notification_attention_state",
    "notification_attention_edges",
    "notification_deliveries",
)


def _seed_notification_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO notification_preferences"
        "(subject_key, notification_type, enabled, updated_at) "
        "VALUES ('tickets', 'awaiting_reply', 1, 1)"
    )
    conn.execute(
        "INSERT INTO notification_attention_state"
        "(subject_kind, subject_id, notification_type, active, generation) "
        "VALUES ('ticket', 't_kept', 'awaiting_reply', 1, 3)"
    )
    conn.execute(
        "INSERT INTO notification_attention_edges"
        "(subject_kind, subject_id, notification_type, generation, occurred_at) "
        "VALUES ('ticket', 't_kept', 'awaiting_reply', 3, 7)"
    )
    conn.execute(
        "INSERT INTO notification_push_subscriptions"
        "(subscription_id, endpoint, p256dh, auth, created_at, updated_at) "
        "VALUES ('sub_kept', 'https://push.example/kept', 'p', 'a', 1, 1)"
    )
    conn.execute(
        "INSERT INTO notification_deliveries"
        "(subject_kind, subject_id, notification_type, generation, subscription_id, "
        "title, body, route, tag, created_at, status, next_attempt_at) "
        "VALUES ('ticket', 't_kept', 'awaiting_reply', 3, 'sub_kept', 'Panels', 'body', "
        "'/', 'tag', 1, 'pending', 1)"
    )


def test_widening_the_notification_type_keeps_every_row_index_and_foreign_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four tables restrict `notification_type` with a CHECK, and SQLite cannot alter one.

    So the revision rebuilds all four, and a rebuild is a DROP: whatever the new table
    does not declare is gone, and nothing afterwards is left dangling to notice it. This
    builds the four as they were before the revision, puts a row in each, and reads the
    tables back after the revision has run against them.
    """
    shipped = db_module.MIGRATIONS_DIRECTORY / "versions"
    tree = _migration_tree(tmp_path, monkeypatch)
    conn = connect(str(tmp_path / "widened.db"))
    create_schema(conn)
    _seed_notification_rows(conn)
    before = {table: _table_structure(conn, table) for table in _NOTIFICATION_TABLES}
    for table in _NOTIFICATION_TABLES:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        assert "awaiting_answer" not in sql

    # Every shipped revision above the baseline, so this keeps working as the chain grows.
    for revision in sorted(shipped.glob("*.py")):
        shutil.copy(revision, tree / "versions" / revision.name)
    create_schema(conn)

    assert _revision(conn) == "an_ask_is_its_own_notification"
    for table in _NOTIFICATION_TABLES:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        assert "'awaiting_answer'" in sql
        assert _table_structure(conn, table) == before[table]
        kept = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE notification_type = 'awaiting_reply'"
        ).fetchone()[0]
        assert kept == 1, f"{table} lost its row"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO {table} (subject_kind, subject_id, notification_type) "
                "VALUES ('ticket', 't_probe', 'not_a_notification_type')"
                if table != "notification_preferences"
                else "INSERT INTO notification_preferences "
                "(subject_key, notification_type, enabled, updated_at) "
                "VALUES ('tickets', 'not_a_notification_type', 1, 1)"
            )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%__rebuilt'"
        ).fetchone()[0]
        == 0
    )
    conn.close()
