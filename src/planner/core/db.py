"""Database connections, and the one door that brings a database up to the current schema.

WAL mode, foreign keys on. Booleans are INTEGER 0/1; JSON is TEXT holding canonical JSON;
all times are INTEGER unix seconds; all dates are TEXT ISO. Column names match the
contract dataclass field names one-for-one.

Every connection opened here also announces its own commits on the process-wide change
signal, so nothing a writer does has to remember to say it wrote. Every writer comes
through this door, so there is no commit the signal does not carry.

The schema itself is not written here. It lives in the migration history under
``migrations/``, whose first revision is the schema as the old hand-written migration
ladder left it.
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
from planner.projects import data as projects_data

MIGRATIONS_DIRECTORY: Final = Path(__file__).resolve().parent / "migrations"
BASELINE_REVISION: Final = "baseline_v37"

# The schema version the old hand-written migration ladder finished at. A database that
# carries this marker and has no Alembic version table is one the ladder built, and is
# adopted at the baseline revision. Anything older is brought up by a checkout that still
# has the ladder; this build does not carry those steps.
LAST_HAND_WRITTEN_SCHEMA_VERSION: Final = 37

# What a database the ladder built contains. Frozen alongside the version above: these
# describe the schema as it was when Alembic took over, and are read only when adopting
# such a database, so they never follow later schema changes.
PRE_ALEMBIC_TABLE_NAMES: Final = frozenset(
    {
        "conversation_session_bindings",
        "day_tickets",
        "days",
        "employee_configuration_catalog_cache",
        "employee_conversations",
        "employee_step_runs",
        "events",
        "ideas",
        "links",
        "pending_worker_context",
        "projects",
        "sprint_items",
        "sprints",
        "ticket_conversation_projections",
        "tickets",
    }
)

# Checked for the same reason as the tables, and worth checking separately: the ladder
# wrote its version marker before creating indexes, so a database could carry the marker
# while still missing one. Adopting that quietly would leave `tickets.alias` without the
# unique index that keeps aliases unique.
PRE_ALEMBIC_INDEX_NAMES: Final = frozenset(
    {
        "idx_employee_step_runs_one_running",
        "idx_events_entity",
        "idx_ideas_project_id",
        "idx_links_to",
        "idx_sprint_items_project_id",
        "idx_tickets_alias",
        "idx_tickets_project_id",
        "idx_tickets_stage",
        "idx_tickets_worker_type_stage",
    }
)


def _statement_commits(sql: str) -> bool:
    """Whether this statement is the one that ends a transaction by keeping its work.

    Transactions here always end with a literal ``COMMIT``; a ``ROLLBACK`` throws the
    work away and must stay silent.
    """
    return sql.strip().lower().startswith("commit")


class ChangeSignallingConnection(sqlite3.Connection):
    """A connection that announces every write it commits, once each.

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
    fails, announces nothing.
    """

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        had_open_transaction = self.in_transaction
        rows_changed_before = self.total_changes
        cursor = super().execute(sql, parameters)
        if self.in_transaction:
            return cursor
        if had_open_transaction:
            if _statement_commits(sql):
                change_signal.emit()
        elif self.total_changes != rows_changed_before:
            change_signal.emit()
        return cursor

    def commit(self) -> None:
        had_open_transaction = self.in_transaction
        super().commit()
        if had_open_transaction:
            change_signal.emit()


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
    if conn.in_transaction:
        raise RuntimeError(
            "create_schema needs a connection with no transaction open, because migrations "
            "run on their own connection and would wait on this one's lock"
        )
    _upgrade_to_current_schema(_main_database_path(conn), _busy_timeout_ms(conn))
    projects_data.seed_default_projects(conn)


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
            _adopt_database_built_before_alembic(connection)
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


def _adopt_database_built_before_alembic(connection: Connection) -> None:
    """Give a database the old ladder built the version marker Alembic reads.

    A new database and a database Alembic already tracks both need nothing here — the
    upgrade that follows builds the first and advances the second. A database with tables
    but no version table came from the ladder, and is adopted at the baseline once it is
    established that it really holds the schema the baseline describes.
    """
    tables = {
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    schema_version = int(connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)

    if "alembic_version" in tables:
        # Alembic writes a row whenever it stamps or upgrades, so an empty version table
        # is not a database it has seen. Left alone, the upgrade below would take it for
        # an empty database and lay the baseline over whatever is really there.
        if not connection.exec_driver_sql("SELECT count(*) FROM alembic_version").scalar():
            raise RuntimeError(
                "this database has a version table with nothing in it, so there is no "
                "saying what schema it holds. Restore it from a backup."
            )
        return

    if not tables:
        if schema_version:
            raise RuntimeError(
                f"this database has no tables but is marked as schema version "
                f"{schema_version}, so it is not the empty database it looks like. "
                "Restore it from a backup."
            )
        return

    if schema_version != LAST_HAND_WRITTEN_SCHEMA_VERSION:
        raise RuntimeError(
            f"this database is at schema version {schema_version}, and this build starts at "
            f"version {LAST_HAND_WRITTEN_SCHEMA_VERSION}. Open it once with a checkout that "
            "still has the hand-written migrations, then come back."
        )
    indexes = {
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = sorted(
        (PRE_ALEMBIC_TABLE_NAMES - tables) | (PRE_ALEMBIC_INDEX_NAMES - indexes)
    )
    unexpected = sorted(tables - PRE_ALEMBIC_TABLE_NAMES)
    if missing or unexpected:
        raise RuntimeError(
            f"this database is marked as schema version {LAST_HAND_WRITTEN_SCHEMA_VERSION} but "
            f"does not hold that schema: missing {missing}, unexpected tables {unexpected}"
        )
    command.stamp(_alembic_config(connection), BASELINE_REVISION)
