import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

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

# Every table, index and trigger a current build holds: the baseline plus what the
# revisions after it add.
CURRENT_SCHEMA_OBJECT_COUNT = 122

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
CLAIM_REVISION_COLUMN = ("worker_step_claim_revision", "INTEGER", 1, "0", 0)
GUIDANCE_COLUMN = ("guidance", "TEXT", 1, "''", 0)


def _revision(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def head_revision() -> str:
    """The revision this build finishes at, which moves as revisions are added."""
    config = Config()
    config.set_main_option("script_location", str(db_module.MIGRATIONS_DIRECTORY))
    return str(ScriptDirectory.from_config(config).get_current_head())


def _build_database_at_the_pre_collapse_head(path: Path) -> sqlite3.Connection:
    """A database as the collapsed chain left it, which is what the live database becomes.

    The schema at that head is the schema the baseline builds, so the way to make one is
    to build the baseline and then say which revision the database came from. The stale
    marker the pre-Alembic ladder wrote is set too, because the live database still has it.
    """
    conn = connect(str(path))
    # Build the actual baseline, rather than reverse current migrations by hand.
    # This preserves the integrity check against unknown schema objects on adoption.
    with tempfile.TemporaryDirectory() as temporary:
        tree = Path(temporary)
        (tree / "versions").mkdir()
        shutil.copy(db_module.MIGRATIONS_DIRECTORY / "env.py", tree / "env.py")
        shutil.copy(
            db_module.MIGRATIONS_DIRECTORY / "versions" / "baseline_2026_09_schema.py",
            tree / "versions" / "baseline_2026_09_schema.py",
        )
        with patch.object(db_module, "MIGRATIONS_DIRECTORY", tree):
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live database, after the build before this one carried it to that head."""
    conn = _build_database_at_the_pre_collapse_head(tmp_path / "at-the-old-head.db")
    _insert_ticket(conn, "t_carried", "Carried over", ticket_status="agent")
    objects_before = _schema_objects(conn)
    # This historical fixture proves adoption through the last pre-cutover revision.
    # It does not claim to represent the reviewed planning-retirement source.
    _migration_tree_through(tmp_path, monkeypatch, "a_refused_codex_login_outlives_a_restart")

    create_schema(conn)

    assert _revision(conn) == head_revision()
    removed_objects = {
        "idx_sprint_items_supervisor_agent_key",
        "sprint_items_supervisor_create",
        "sprint_items_supervisor_insert_guard",
        "sprint_items_supervisor_update_guard",
    }
    assert set(objects_before) - removed_objects <= set(_schema_objects(conn))
    assert tuple(
        conn.execute(
            "SELECT title, worker_step_claim FROM tickets WHERE id = 't_carried'"
        ).fetchone()
    ) == ("Carried over", "out")
    # Adoption clears the marker, so this database and a database built from nothing by
    # this build are the same database.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()


def test_current_cutover_refuses_ordinary_only_historical_fixture_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical adoption coverage does not bypass the current reviewed-source guard."""
    conn = connect(str(tmp_path / "ordinary-only-pre-cutover.db"))
    with monkeypatch.context() as historical:
        _migration_tree_through(
            tmp_path,
            historical,
            "a_refused_codex_login_outlives_a_restart",
        )
        create_schema(conn)
    _insert_ticket(conn, "t_ordinary", "Ordinary synthetic historical work")
    conn.commit()
    original = dict(conn.execute("SELECT * FROM tickets WHERE id='t_ordinary'").fetchone())
    schema_before = _schema_objects(conn)
    revision_before = _revision(conn)

    with pytest.raises(RuntimeError, match="cohort changed"):
        create_schema(conn)

    assert _revision(conn) == revision_before == "a_refused_codex_login_outlives_a_restart"
    assert _schema_objects(conn) == schema_before
    assert dict(conn.execute("SELECT * FROM tickets WHERE id='t_ordinary'").fetchone()) == original
    conn.close()




def test_manager_wake_migration_backfills_current_unresolved_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _build_database_at_the_pre_collapse_head(tmp_path / "wake-backfill.db")
    conn.execute("DELETE FROM alembic_version")
    conn.execute("INSERT INTO alembic_version VALUES ('an_ask_is_its_own_notification')")
    conn.execute(
        "INSERT INTO agents(agent_key,conversation_id) VALUES "
        "('sprint_item_supervisor_si_backfill',NULL)"
    )
    conn.execute(
        "INSERT INTO sprint_items(id,title,project_id,supervisor_agent_key,"
        "supervisor_backend,supervisor_model,supervisor_reasoning_effort,created_at,updated_at) "
        "VALUES ('si_backfill','Backfill','project_vylo',"
        "'sprint_item_supervisor_si_backfill','codex','gpt-5.6-sol','medium',1,1)"
    )
    conn.execute(
        "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,project_id,"
        "sprint_item_id,ceiling,ceiling_holder,field_values,pending_proposal,created_at,"
        "updated_at) VALUES ('t_proposal','Proposal','coding','codex',"
        "'needs_success_condition','project_vylo','si_backfill','needs_success_condition',"
        "?, '{}', ?, 1, 2)",
        (
            '{"kind":"sprint_item","id":"si_backfill"}',
            '{"field":"success_condition","body":"Ready","proposed_by":"worker","created_at":2}',
        ),
    )
    conn.execute(
        "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,project_id,"
        "sprint_item_id,ceiling,ceiling_holder,field_values,worker_step_claim,"
        "worker_step_claim_revision,worker_step_claim_changed_at,created_at,updated_at) "
        "VALUES ('t_error','Error','coding','codex','needs_success_condition','project_vylo',"
        "'si_backfill','needs_success_condition',?, '{}','errored',3,3,1,3)",
        ('{"kind":"owner","id":"owner"}',),
    )

    # Exercise this revision's backfill against its own historical schema only.
    _migration_tree_through(tmp_path, monkeypatch, "wake_sprint_item_managers")
    create_schema(conn)
    assert _revision(conn) == head_revision() == "wake_sprint_item_managers"

    rows = conn.execute(
        "SELECT ticket_id,source_kind,source_revision FROM manager_wakes ORDER BY ticket_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("t_error", "worker_error", 3),
        ("t_proposal", "proposal", 1),
    ]
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


def _migration_tree_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    head: str,
) -> Path:
    """Use the real ancestor chain for a test of a particular historical revision."""
    shipped = db_module.MIGRATIONS_DIRECTORY
    tree = _migration_tree(tmp_path, monkeypatch)
    for revision in ScriptDirectory(str(shipped)).walk_revisions(base="base", head=head):
        shutil.copy(revision.path, tree / "versions" / Path(revision.path).name)
    assert head_revision() == head
    return tree


def _database_at_previous_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[sqlite3.Connection, Path]:
    """Build the exact schema immediately before the storage-removal revision."""
    shipped = db_module.MIGRATIONS_DIRECTORY / "versions"
    tree = _migration_tree(tmp_path, monkeypatch)
    for revision in ScriptDirectory(str(shipped.parent)).walk_revisions(
        base="base", head="wake_sprint_item_managers"
    ):
        shutil.copy(revision.path, tree / "versions" / Path(revision.path).name)
    conn = connect(str(tmp_path / "previous-head.db"))
    create_schema(conn)
    assert _revision(conn) == "wake_sprint_item_managers"
    return conn, shipped / "remove_redundant_ticket_and_item_storage.py"


def _database_before_principal_wakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[sqlite3.Connection, Path]:
    """Build the schema immediately before notices gained principal targets."""
    shipped = db_module.MIGRATIONS_DIRECTORY / "versions"
    tree = _migration_tree(tmp_path, monkeypatch)
    for revision in ScriptDirectory(str(shipped.parent)).walk_revisions(
        base="base", head="remove_redundant_ticket_item_storage"
    ):
        shutil.copy(revision.path, tree / "versions" / Path(revision.path).name)
    conn = connect(str(tmp_path / "before-principal-wakes.db"))
    create_schema(conn)
    assert _revision(conn) == "remove_redundant_ticket_item_storage"
    return conn, shipped / "address_manager_wakes_to_principals.py"


def test_principal_wake_migration_preserves_sources_batches_members_and_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, revision = _database_before_principal_wakes(tmp_path, monkeypatch)
    tree = db_module.MIGRATIONS_DIRECTORY
    conn.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES ('p','Project',1,1)")
    conn.execute(
        "INSERT INTO sprint_items(id,title,project_id,supervisor_backend,supervisor_model,"
        "supervisor_reasoning_effort,created_at,updated_at) "
        "VALUES ('si','Item','p','codex','model','medium',1,1)"
    )
    conn.execute(
        "INSERT INTO manager_wakes(id,sprint_item_id,ticket_id,source_kind,source_revision,"
        "summary,created_at,closed_at) VALUES (11,'si',NULL,'proposal',1,'proposal',2,NULL)"
    )
    for batch_id, status in ((21, "accepted"), (22, "uncertain"), (23, "delivered")):
        conn.execute(
            "INSERT INTO manager_wake_batches(id,sprint_item_id,sender_message_id,message,"
            "status,conversation_id,process_token,created_at,updated_at) "
            "VALUES (?,'si',?,?,?,'conv','old-process',3,4)",
            (batch_id, f"message-{batch_id}", "review", status),
        )
        conn.execute(
            "INSERT INTO manager_wake_batch_members(batch_id,wake_id) VALUES (?,11)",
            (batch_id,),
        )
    conn.commit()

    shutil.copy(revision, tree / "versions" / revision.name)
    create_schema(conn)

    assert _revision(conn) == "address_manager_wakes_to_principals"
    wake = conn.execute(
        "SELECT target_kind,target_id,source_conversation_id FROM manager_wakes WHERE id=11"
    ).fetchone()
    assert tuple(wake) == ("sprint_item", "si", None)
    batches = conn.execute(
        "SELECT id,status,target_kind,target_id FROM manager_wake_batches ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in batches] == [
        (21, "accepted", "sprint_item", "si"),
        (22, "uncertain", "sprint_item", "si"),
        (23, "delivered", "sprint_item", "si"),
    ]
    members = conn.execute(
        "SELECT batch_id,wake_id FROM manager_wake_batch_members ORDER BY batch_id"
    ).fetchall()
    assert [tuple(row) for row in members] == [(21, 11), (22, 11), (23, 11)]


def test_storage_migration_preserves_effective_projects_supervisors_and_legacy_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, revision = _database_at_previous_head(tmp_path, monkeypatch)
    tree = db_module.MIGRATIONS_DIRECTORY
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES ('c_old','codex','/tmp','full',1)"
    )
    conn.execute(
        "INSERT INTO sprint_items(id,title,project_id,created_at,updated_at) "
        "VALUES ('si_custom','Custom','project_vylo',1,1)"
    )
    conn.execute(
        "INSERT INTO agents(agent_key,conversation_id) VALUES ('custom_supervisor','c_old')"
    )
    conn.execute(
        "UPDATE sprint_items SET supervisor_agent_key='custom_supervisor' WHERE id='si_custom'"
    )
    conn.execute("DELETE FROM agents WHERE agent_key='sprint_item_supervisor_si_custom'")
    conn.execute(
        "INSERT INTO notification_attention_state(subject_kind,subject_id,notification_type,"
        "active,generation) VALUES ('agent','custom_supervisor','awaiting_reply',1,1)"
    )
    conn.execute(
        "INSERT INTO sprint_items(id,title,project_id,kind,created_at,updated_at) "
        "VALUES ('si_legacy','Legacy','project_personal','other',1,1)"
    )
    conn.execute(
        "INSERT INTO sprints(id,name,date_start,date_end,created_at,updated_at) "
        "VALUES ('sp_kept','Kept','2026-09-01','2026-09-07',1,1)"
    )
    conn.execute(
        "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,priority,project_id,"
        "sprint_item_id,recap,ceiling,field_values,worker_step_claim,"
        "worker_step_claim_revision,worker_step_claim_changed_at,created_at,updated_at) VALUES "
        "('t_parented','Parented','coding','codex','needs_brief','P3','project_vylo',"
        "'si_legacy','','needs_success_condition','{}','errored',2,7,1,1),"
        "('t_standalone','Standalone','coding','codex','needs_brief','P3','project_vylo',"
        "NULL,'','needs_success_condition','{}','none',0,0,1,1)"
    )
    conn.execute("INSERT INTO sprint_outcomes(sprint_id,outcome_id) VALUES ('sp_kept','si_legacy')")

    shutil.copy(revision, tree / "versions" / revision.name)
    create_schema(conn)

    assert _revision(conn) == "remove_redundant_ticket_item_storage"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key='sprint_item_supervisor_si_custom'"
        ).fetchone()[0]
        == "c_old"
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM notification_attention_state "
            "WHERE subject_id='sprint_item_supervisor_si_custom'"
        ).fetchone()[0]
        == 1
    )
    legacy = conn.execute(
        "SELECT supervisor_backend,supervisor_model,supervisor_reasoning_effort "
        "FROM sprint_items WHERE id='si_legacy'"
    ).fetchone()
    assert tuple(legacy) == ("codex", "gpt-5.6-sol", "medium")
    assert (
        conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key='sprint_item_supervisor_si_legacy'"
        ).fetchone()[0]
        is None
    )
    assert (
        conn.execute("SELECT project_id FROM tickets WHERE id='t_parented'").fetchone()[0] is None
    )
    assert (
        conn.execute("SELECT project_id FROM tickets WHERE id='t_standalone'").fetchone()[0]
        == "project_vylo"
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM sprint_outcomes "
            "WHERE sprint_id='sp_kept' AND outcome_id='si_legacy'"
        ).fetchone()[0]
        == 1
    )
    assert tuple(
        conn.execute(
            "SELECT source_kind,source_revision FROM manager_wakes WHERE ticket_id='t_parented'"
        ).fetchone()
    ) == ("worker_error", 2)
    assert "worker_step_claim_changed_at" not in {
        str(row[1]) for row in conn.execute("PRAGMA table_info(tickets)")
    }
    conn.close()


def test_storage_migration_refuses_derived_supervisor_collision_transactionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, revision = _database_at_previous_head(tmp_path, monkeypatch)
    tree = db_module.MIGRATIONS_DIRECTORY
    conn.execute(
        "INSERT INTO sprint_items(id,title,project_id,created_at,updated_at) "
        "VALUES ('si_collision','Collision','project_vylo',1,1)"
    )
    conn.execute("INSERT INTO agents(agent_key,conversation_id) VALUES ('custom_collision',NULL)")
    conn.execute(
        "UPDATE sprint_items SET supervisor_agent_key='custom_collision' WHERE id='si_collision'"
    )
    shutil.copy(revision, tree / "versions" / revision.name)

    with pytest.raises(RuntimeError, match="si_collision.*already exists"):
        create_schema(conn)

    assert _revision(conn) == "wake_sprint_item_managers"
    assert (
        conn.execute(
            "SELECT supervisor_agent_key FROM sprint_items WHERE id='si_collision'"
        ).fetchone()[0]
        == "custom_collision"
    )
    assert "kind" in {str(row[1]) for row in conn.execute("PRAGMA table_info(sprint_items)")}
    conn.close()


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

    assert _revision(conn) == head_revision()
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
        conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%__rebuilt'").fetchone()[
            0
        ]
        == 0
    )
    conn.close()


def test_model_resolution_migration_preserves_saved_choices_and_removes_pinned_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shipped = db_module.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "native_usage_prices")
    conn = connect(str(tmp_path / "models.db"))
    create_schema(conn)
    project_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO agents(agent_key,conversation_id) VALUES ('sprint_item_supervisor_old',NULL)"
    )
    conn.execute(
        "INSERT INTO sprint_items(id,title,body,priority,project_id,supervisor_backend,"
        "supervisor_model,supervisor_reasoning_effort,created_at,updated_at) "
        "VALUES ('old','Existing','', 'P3',?,'codex','gpt-5.6-sol','high',1,1)",
        (project_id,),
    )
    conn.execute(
        "CREATE TRIGGER sprint_items_supervisor_create AFTER INSERT ON sprint_items BEGIN "
        "UPDATE sprint_items SET supervisor_model='gpt-5.6-sol' WHERE id=NEW.id; END"
    )
    before = tuple(conn.execute("SELECT * FROM sprint_items WHERE id='old'").fetchone())
    shutil.copy(shipped / "versions" / "current_model_variants.py", tree / "versions")
    create_schema(conn)
    assert tuple(conn.execute("SELECT * FROM sprint_items WHERE id='old'").fetchone()) == before
    assert conn.execute(
        "SELECT agent_key FROM agents WHERE agent_key='sprint_item_supervisor_old'"
    ).fetchone()
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='sprint_items_supervisor_create'"
        ).fetchone()
        is None
    )
    assert conn.execute("SELECT COUNT(*) FROM model_variant_resolutions").fetchone()[0] == 0
    conn.close()


def test_priority_removal_preserves_other_values_constraints_and_dependents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped = db_module.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "chief_recap")
    conn = connect(str(tmp_path / "without-priority.db"))
    create_schema(conn)
    conn.execute("UPDATE chief_settings SET recap='Keep the owner recap'")
    chief_before = tuple(conn.execute("SELECT * FROM chief_settings").fetchone())
    project_id = str(conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0])
    conn.execute(
        "INSERT INTO sprint_items(id,title,project_id,supervisor_backend,supervisor_model,"
        "supervisor_reasoning_effort,created_at,updated_at) "
        "VALUES ('si_kept','Item',?,'codex','model','medium',1,2)",
        (project_id,),
    )
    _insert_ticket(conn, "t_kept", "Kept")
    _insert_ticket(conn, "t_child", "Child")
    conn.execute(
        "UPDATE tickets SET sprint_item_id='si_kept',orchestrator_kind='sprint_item',"
        "project_id=NULL,priority='P0' WHERE id='t_kept'"
    )
    conn.execute("INSERT INTO ticket_blocks VALUES ('t_kept','t_child')")
    conn.execute(
        "INSERT INTO scheduled_ticket_schedules(id,enabled,cadence,local_time,title,"
        "worker_type,priority,placement_mode,created_at,updated_at) "
        "VALUES ('schedule_kept',1,'daily','09:00','Keep schedule','coding','P1','backlog',3,4)"
    )
    tables = ("projects", "sprint_items", "tickets", "scheduled_ticket_schedules")
    before = {}
    structures = {}
    for table in tables:
        columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
        retained = ",".join(f'"{column}"' for column in columns if column != "priority")
        before[table] = (
            retained,
            list(conn.execute(f"SELECT {retained} FROM {table} ORDER BY id")),
        )
        structures[table] = _table_structure(conn, table)
    dependents = _schema_objects(conn)
    conn.commit()
    shutil.copy(shipped / "versions" / "remove_priority.py", tree / "versions")
    create_schema(conn)

    for table in tables:
        projection, rows = before[table]
        assert list(conn.execute(f"SELECT {projection} FROM {table} ORDER BY id")) == rows
        structure = _table_structure(conn, table)
        assert structure["columns"] == [
            column
            for column in cast(
                list[tuple[str, str, int, str | None, int]], structures[table]["columns"]
            )
            if column[0] != "priority"
        ]
        assert structure["foreign_keys"] == structures[table]["foreign_keys"]
        assert structure["indexes"] == structures[table]["indexes"]
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        assert "priority" not in sql
    assert tuple(conn.execute("SELECT * FROM chief_settings").fetchone()) == chief_before
    after = _schema_objects(conn)
    assert {key: value for key, value in after.items() if key not in tables} == {
        key: value for key, value in dependents.items() if key not in tables
    }
    assert [tuple(row) for row in conn.execute("SELECT * FROM ticket_blocks")] == [
        ("t_kept", "t_child")
    ]
    assert list(conn.execute("PRAGMA foreign_key_check")) == []
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tickets SET worker_step_claim='invalid' WHERE id='t_kept'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE scheduled_ticket_schedules SET enabled=5 WHERE id='schedule_kept'")
    conn.close()
