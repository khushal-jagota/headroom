"""Database connections, and the one door that brings a database up to the current schema.

WAL mode, foreign keys on. Booleans are INTEGER 0/1; JSON is TEXT holding canonical JSON;
all times are INTEGER unix seconds; all dates are TEXT ISO. Column names match the
contract dataclass field names one-for-one.

Every connection opened here also announces its own commits on the process-wide change
signal, so nothing a writer does has to remember to say it wrote. A writer with a reason
that no reader is waiting for its rows closes its transaction with
``commit_without_change_signal`` instead, which is the one way past it.

The schema itself is not written here. It lives in the migration history under
``migrations/``, whose first revision is the whole schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Final

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import URL

from planner.core import change_signal

MIGRATIONS_DIRECTORY: Final = Path(__file__).resolve().parent / "migrations"
BASELINE_REVISION: Final = "baseline_2026_09"

# The head of the sixty-revision chain the baseline replaced. A database that carries this
# revision holds exactly the schema the baseline builds, and every row rewrite those
# revisions performed, so it is adopted at the baseline rather than rebuilt. Frozen: it
# names a moment in history, so later schema work never changes it.
PRE_COLLAPSE_HEAD_REVISION: Final = "settled_stage_and_field_ids"

# Every table, index and trigger a database at that head holds. Frozen alongside the
# revision above, and read only when adopting such a database, so it describes that one
# moment in history and never follows later schema changes.
PRE_COLLAPSE_HEAD_SCHEMA_OBJECT_NAMES: Final = frozenset(
    {
        "agents",
        "backend_model_enablement",
        "backend_usage_snapshots",
        "backend_usage_windows",
        "chief_settings",
        "conversation_error_acknowledgements",
        "conversation_events",
        "conversations",
        "day_tickets",
        "days",
        "feedback_notes",
        "ideas",
        "idx_conversation_events_kind_recipient",
        "idx_conversation_events_sender_message_id",
        "idx_conversations_automatic_compaction_due",
        "idx_feedback_notes_state_created",
        "idx_feedback_notes_ticket_id",
        "idx_ideas_project_id",
        "idx_notification_deliveries_due",
        "idx_scheduled_ticket_occurrences_created_at",
        "idx_scheduled_ticket_schedules_slot",
        "idx_sprint_items_project_id",
        "idx_sprint_items_supervisor_agent_key",
        "idx_sprint_outcomes_outcome_id",
        "idx_ticket_blocks_blocked_ticket_id",
        "idx_ticket_conversations_ticket_id",
        "idx_tickets_project_id",
        "idx_tickets_stage",
        "idx_tickets_worker_type_stage",
        "managed_skill_versions",
        "managed_skill_versions_no_delete",
        "managed_skill_versions_no_update",
        "managed_skills",
        "notification_attention_edges",
        "notification_attention_state",
        "notification_deliveries",
        "notification_preferences",
        "notification_push_subscriptions",
        "notification_web_push_identity",
        "projects",
        "scheduled_ticket_occurrences",
        "scheduled_ticket_schedules",
        "sprint_items",
        "sprint_items_supervisor_create",
        "sprint_items_supervisor_insert_guard",
        "sprint_items_supervisor_update_guard",
        "sprint_outcomes",
        "sprints",
        "ticket_blocks",
        "ticket_conversations",
        "ticket_paired_stage_openers",
        "ticket_revision_feedback",
        "tickets",
        "worker_step_skill_bindings",
        "worker_types",
    }
)


def _statement_commits(sql: str) -> bool:
    """Whether this statement is the one that ends a transaction by keeping its work.

    Transactions here always end with a literal ``COMMIT``; a ``ROLLBACK`` throws the
    work away and must stay silent.
    """
    return sql.strip().lower().startswith("commit")


class ChangeSignallingConnection(sqlite3.Connection):
    """A connection that announces the writes it commits, once each.

    This is the door writers already go through, so no writer has to remember to
    announce anything. A write commits in one of two ways here, and both are watched:

    - inside a transaction the caller opened, when the closing ``COMMIT`` runs;
    - on its own. These connections leave transaction control to the caller, so a
      statement run with nothing open commits the moment it returns. Whether it was
      really a write is the row count: a statement that leaves the stored rows exactly
      as they were — a read, an update that matched nothing, a schema change — says
      nothing, because there is nothing for a reader to come back for.

    The announcement happens after the statement returns, when the write lock is
    already released and the new rows are readable. A rollback, or a commit that
    fails, announces nothing. A committed transaction that changed no rows — a
    reconciliation pass that found nothing to do — also announces nothing: several
    background loops open a transaction on every pass and are themselves woken by
    this signal, so an empty commit that announced would wake the loop that made it
    and the tick would collapse into a busy-spin.

    A caller that knows no reader is waiting for its rows closes its transaction with
    ``commit_without_change_signal``, which goes around this and stays quiet.
    """

    # Rows changed when the currently open transaction began; None when no
    # transaction is open or it was opened outside this method (then a commit
    # announces, conservatively).
    _rows_changed_at_transaction_start: int | None = None

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        had_open_transaction = self.in_transaction
        rows_changed_before = self.total_changes
        cursor = super().execute(sql, parameters)
        if self.in_transaction:
            if not had_open_transaction:
                self._rows_changed_at_transaction_start = rows_changed_before
            return cursor
        if had_open_transaction:
            if _statement_commits(sql) and self._open_transaction_wrote():
                change_signal.emit()
            self._rows_changed_at_transaction_start = None
        elif self.total_changes != rows_changed_before:
            change_signal.emit()
        return cursor

    def commit(self) -> None:
        had_open_transaction = self.in_transaction
        wrote = self._open_transaction_wrote()
        super().commit()
        self._rows_changed_at_transaction_start = None
        if had_open_transaction and wrote:
            change_signal.emit()

    def _open_transaction_wrote(self) -> bool:
        baseline = self._rows_changed_at_transaction_start
        return baseline is None or self.total_changes != baseline


def commit_without_change_signal(conn: sqlite3.Connection) -> None:
    """Keep an open transaction's work without announcing it.

    The signal tells every reader to come back for what changed, and a reader that comes
    back refetches its whole screen. So a write nobody is waiting for is worth a quiet
    commit: internal history maintenance that no screen shows, and the conversation rows
    that only an open conversation shows, which is fed its rows directly rather than
    through the signal.

    Staying quiet is decided at the write, one commit at a time. Anything that might have
    a reader commits normally.
    """
    sqlite3.Connection.execute(conn, "COMMIT")


def connect(db_path: str, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path,
        isolation_level=None,
        timeout=busy_timeout_ms / 1000,
        factory=ChangeSignallingConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Bring the connected database up to the current schema and seed default projects.

    Safe to call on every open: a new database is built, a current one is left alone.
    """
    # Seeding is the only thing here that knows a domain, and domains open connections
    # through this module — so these are imported where they are used, leaving this module
    # importable on its own.
    from planner.managed_skills import seed_packaged_skills, write_skill_home
    from planner.notifications import data as notifications_data
    from planner.projects import data as projects_data
    from planner.scheduled_tickets.data import seed_shipped_schedules
    from planner.worker_types.configuration import load_worker_runtime_definitions
    from planner.worker_types.shipped import seed_chief_settings, seed_shipped_worker_types
    from planner.worker_types.store import worker_types_table_exists

    if conn.in_transaction:
        raise RuntimeError(
            "create_schema needs a connection with no transaction open, because migrations "
            "run on their own connection and would wait on this one's lock"
        )
    _upgrade_to_current_schema(_main_database_path(conn), _busy_timeout_ms(conn))
    projects_data.seed_default_projects(conn)
    # Migration-unit fixtures deliberately replace the tree with a historical
    # subset. Seed the current subsystem only when that tree actually installed it.
    notification_schema_exists = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'notification_web_push_identity'"
    ).fetchone()
    if notification_schema_exists is not None:
        notifications_data.get_or_create_web_push_identity(conn, 0)
    # The Worker types this process runs on, and the skill files agents read, both come
    # from this database. Loading them here is what makes opening a database enough.
    if worker_types_table_exists(conn):
        # The rows a brand-new database needs. Each one declines to touch a table that
        # already holds rows, so an existing database keeps what its owner has edited.
        # One transaction for all three, because a Worker type names a skill: a second
        # process must not find the types seeded and the skills still going in.
        conn.execute("BEGIN IMMEDIATE")
        try:
            seed_packaged_skills(conn)
            seed_shipped_worker_types(conn)
            seed_chief_settings(conn)
            seed_shipped_schedules(conn)
            notifications_data.seed_shipped_notification_preferences(conn)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.commit()
        write_skill_home(conn, Path(_main_database_path(conn)).parent)
        load_worker_runtime_definitions(conn)


def _main_database_path(conn: sqlite3.Connection) -> str:
    for row in conn.execute("PRAGMA database_list"):
        if str(row[1]) != "main":
            continue
        path = "" if row[2] is None else str(row[2])
        if not path:
            raise RuntimeError(
                "create_schema needs a database stored in a file; this connection's database "
                "is in memory or temporary, which a second connection cannot reach"
            )
        return path
    raise RuntimeError("connection has no main database")


def _busy_timeout_ms(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA busy_timeout").fetchone()[0])


def _upgrade_to_current_schema(db_path: str, busy_timeout_ms: int) -> None:
    """Run every migration the database has not seen yet, as one all-or-nothing step."""
    engine = _migration_engine(db_path, busy_timeout_ms)
    try:
        with engine.begin() as connection:
            _adopt_database_left_by_the_collapsed_chain(connection)
            command.upgrade(_alembic_config(connection), "head")
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"migrations left foreign key violations: {violations!r}")
    finally:
        engine.dispose()


def _migration_engine(db_path: str, busy_timeout_ms: int) -> Engine:
    engine = create_engine(
        URL.create(drivername="sqlite", database=db_path),
        # Take transaction control away from the driver. Left to itself it starts a
        # transaction only for row changes, so CREATE TABLE runs outside one and a failed
        # migration leaves its half-built tables behind.
        connect_args={"isolation_level": None},
    )

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        # Changing a CHECK constraint on SQLite means rebuilding the table, and a rebuild
        # drops the table it is rebuilding. With foreign keys enforced that DROP fires
        # ON DELETE CASCADE and silently empties every dependent table, so enforcement is
        # off while migrations run and the foreign_key_check afterwards is what catches a
        # migration that left the record inconsistent.
        dbapi_connection.execute("PRAGMA foreign_keys=OFF")

    @event.listens_for(engine, "begin")
    def _begin_immediate(connection: Connection) -> None:
        # Take the write lock before reading anything, so two processes starting at the
        # same time cannot both conclude the database needs building or adopting.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def _alembic_config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    config.attributes["connection"] = connection
    return config


def _schema_object_names(connection: Connection) -> set[str]:
    """Every table, index and trigger a database holds, excluding SQLite's own."""
    return {
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger') AND name NOT LIKE 'sqlite_%'"
        )
    }


def _adopt_database_left_by_the_collapsed_chain(connection: Connection) -> None:
    """Give a database the old chain left behind the version marker this build reads.

    A new database and a database already at the baseline both need nothing here — the
    upgrade that follows builds the first and leaves the second alone. A database at the
    head the chain finished at holds the baseline's schema and the row rewrites the chain
    performed, so it is adopted at the baseline once that schema is confirmed.

    Every other state is refused. The revisions that would carry such a database forward
    are not in this build, so the only honest answer is to say which build has them.
    """
    tables = {
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }

    if "alembic_version" not in tables:
        if not tables:
            return
        raise RuntimeError(
            "this database has tables but no version table, so there is no saying what "
            "schema it holds. Open it once with a build from before the migration chain "
            "was collapsed, let it start, then come back."
        )

    revisions = [
        str(row[0]) for row in connection.exec_driver_sql("SELECT version_num FROM alembic_version")
    ]
    if revisions == [BASELINE_REVISION]:
        return
    if revisions != [PRE_COLLAPSE_HEAD_REVISION]:
        held = ", ".join(revisions) if revisions else "nothing"
        raise RuntimeError(
            f"this database is at revision {held}, and this build starts at "
            f"{BASELINE_REVISION}, which it adopts only from {PRE_COLLAPSE_HEAD_REVISION}. "
            "Deploy the build from before the migration chain was collapsed, let it start "
            "once so it carries this database to "
            f"{PRE_COLLAPSE_HEAD_REVISION}, then deploy this build again."
        )

    present = _schema_object_names(connection)
    promised = set(PRE_COLLAPSE_HEAD_SCHEMA_OBJECT_NAMES)
    missing = sorted(promised - present)
    unexpected = sorted(present - promised - {"alembic_version"})
    if missing or unexpected:
        raise RuntimeError(
            f"this database is at revision {PRE_COLLAPSE_HEAD_REVISION} but does not hold "
            f"that schema: missing {missing}, unexpected {unexpected}"
        )
    # purge, because the row already in the version table names a revision this build no
    # longer carries. Without it Alembic tries to resolve that name to plot a path, and
    # fails before it writes anything.
    command.stamp(_alembic_config(connection), BASELINE_REVISION, purge=True)
    # The pre-Alembic ladder left a version marker behind, and no build still reads it.
    # Clearing it as the database is adopted makes an adopted database and a database this
    # build made from nothing the same database.
    connection.exec_driver_sql("PRAGMA user_version=0")
