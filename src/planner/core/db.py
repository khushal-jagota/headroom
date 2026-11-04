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

import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Literal, Self, TypeVar, overload

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import URL

from planner.core import change_signal
from planner.core.config import DB_BUSY_TIMEOUT_MS
from planner.core.transaction_participants import TransactionParticipant, finalize

MIGRATIONS_DIRECTORY: Final = Path(__file__).resolve().parent / "migrations"
BASELINE_REVISION: Final = "canonical_manager_identities"

_CONTROL_TOKEN = re.compile(
    r"\s+|--[^\n]*(?:\n|$)|/\*.*?\*/|"
    r"\"(?:\"\"|[^\"])*\"|'(?:''|[^'])*'|`(?:``|[^`])*`|\[[^]]*\]|[\w$]+|[^\s;]",
    re.DOTALL,
)
_CursorT = TypeVar("_CursorT", bound=sqlite3.Cursor)
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _control_words(sql: str) -> list[str]:
    """Read transaction control only. Never classify domain writes from SQL text."""
    words = []
    for match in _CONTROL_TOKEN.finditer(sql):
        token = match.group()
        if token.isspace() or token == "\ufeff" or token.startswith(("--", "/*")):
            continue
        if token[0] in {'"', "'", "`", "["}:
            quote = token[0]
            token = token[1:-1].replace(quote * 2, quote)
        words.append(token.translate(_ASCII_LOWER))
        if len(words) == 1 and words[0] not in {"savepoint", "release", "rollback"}:
            return words
        if len(words) == 5:
            break
    return words


class ParticipatingCursor(sqlite3.Cursor):
    """Use the connection boundary even for callers that retain a cursor."""

    def execute(self, sql: str, parameters: Any = (), /) -> Self:
        connection = self.connection
        assert isinstance(connection, ChangeSignallingConnection)
        connection._execute(self, sql, parameters)
        return self

    def executemany(self, sql: str, parameters: Any, /) -> Self:
        connection = self.connection
        assert isinstance(connection, ChangeSignallingConnection)
        owns_transaction = not connection.in_transaction
        if owns_transaction:
            connection.execute("BEGIN")
        try:
            sqlite3.Cursor.executemany(self, sql, parameters)
            if owns_transaction:
                connection.commit()
            return self
        except BaseException:
            if owns_transaction:
                connection.rollback()
            elif not connection.in_transaction:
                connection._clear_transaction()
            raise

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        # Match sqlite3's initial implicit commit through the managed boundary.
        # Never call its native script runner: transaction statements inside that
        # runner bypass the Python connection/cursor overrides.
        if self.connection.in_transaction:
            self.connection.commit()
        start = 0
        for index, character in enumerate(sql_script):
            if character == ";" and sqlite3.complete_statement(sql_script[start : index + 1]):
                self.execute(sql_script[start : index + 1])
                start = index + 1
        tail = sql_script[start:]
        if _control_words(tail):
            self.execute(tail)
        return self


class ChangeSignallingConnection(sqlite3.Connection):
    """Finalize participants before commit, then announce committed writes.

    Standalone SQL mutation operations run inside explicit transactions. This
    class classifies SQL operations only; domains determine relevant source rows.
    Base-class transaction calls bypass this protocol and are unsupported.
    DML RETURNING requires an explicit transaction and consumed result rows before
    commit. A standalone unconsumed result fails and rolls back its source write.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._participants: tuple[TransactionParticipant, ...] = ()
        self._finalizing = False
        self._rows_changed_at_transaction_start: int | None = None
        self._savepoints: list[str] = []
        self._savepoint_transaction = False
        self._contexts: list[str | None] = []
        self._context_sequence = 0
        self._control_guard_depth = 0
        self._closed = False
        self._authorizer: Callable[..., int] | None = None
        self._refresh_authorizer()

    def _refresh_authorizer(self) -> None:
        # Installing the dispatcher also expires cached statements. Their old
        # authorization cannot bypass a newly entered control guard.
        super().set_authorizer(self._authorize)

    def _authorize(self, action: int, *arguments: Any) -> int:
        if self._control_guard_depth and action in {
            sqlite3.SQLITE_TRANSACTION,
            sqlite3.SQLITE_SAVEPOINT,
        }:
            return sqlite3.SQLITE_DENY
        if self._authorizer is not None:
            return self._authorizer(action, *arguments)
        return sqlite3.SQLITE_OK

    def set_authorizer(self, authorizer_callback: Callable[..., int] | None) -> None:
        if self._control_guard_depth:
            raise RuntimeError("transaction participants cannot replace the authorizer")
        self._authorizer = authorizer_callback
        self._refresh_authorizer()

    def _check_transaction_control(self) -> None:
        if self._finalizing or self._control_guard_depth:
            raise RuntimeError("transaction participants cannot control transactions")

    def _failed_commit(self, error: BaseException) -> None:
        try:
            if self.in_transaction:
                super().rollback()
        except BaseException as cleanup_error:
            error.add_note(f"commit rollback also failed: {cleanup_error!r}")
        self._clear_transaction()

    def close(self) -> None:
        self._check_transaction_control()
        if self._closed:
            return
        if self.in_transaction:
            self._rollback_failed()
        super().close()
        self._closed = True

    @overload
    def cursor(self, factory: None = None) -> sqlite3.Cursor: ...

    @overload
    def cursor(self, factory: Callable[[sqlite3.Connection], _CursorT]) -> _CursorT: ...

    def cursor(self, factory: Any = None) -> Any:
        if factory is not None and factory is not ParticipatingCursor:
            raise sqlite3.NotSupportedError("custom cursors bypass transaction participation")
        return super().cursor(ParticipatingCursor)

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        return self.cursor().executescript(sql_script)

    def _execute(self, cursor: sqlite3.Cursor, sql: str, parameters: Any) -> sqlite3.Cursor:
        words = _control_words(sql)
        operation = words[0] if words else ""
        if not self.in_transaction and operation in {
            "insert",
            "update",
            "delete",
            "replace",
            "with",
        }:
            self.execute("BEGIN")
            try:
                result = self._execute(cursor, sql, parameters)
                self.commit()
                return result
            except BaseException:
                self.rollback()
                raise
        if (self._finalizing or self._control_guard_depth) and operation in {
            "begin",
            "commit",
            "end",
            "savepoint",
            "release",
            "rollback",
        }:
            raise RuntimeError("transaction participants cannot control transactions")
        had_transaction = self.in_transaction
        before = self.total_changes
        release_index: int | None = None
        if operation == "release" and len(words) > 1:
            name = words[2] if words[1] == "savepoint" and len(words) > 2 else words[1]
            release_index = next(
                (
                    index
                    for index in range(len(self._savepoints) - 1, -1, -1)
                    if self._savepoints[index] == name
                ),
                None,
            )
        commits = operation in {"commit", "end"} or (
            operation == "release" and release_index == 0 and self._savepoint_transaction
        )
        try:
            if commits and had_transaction:
                self._before_commit()
            result = self._sqlite_execute(cursor, sql, parameters)
        except BaseException as error:
            if commits and had_transaction:
                self._failed_commit(error)
            elif had_transaction and not self.in_transaction:
                self._clear_transaction()
            raise
        if operation == "savepoint" and len(words) > 1:
            if not had_transaction:
                self._savepoint_transaction = True
            self._savepoints.append(words[1])
        elif operation == "release" and release_index is not None:
            del self._savepoints[release_index:]
        elif operation == "rollback" and "to" in words:
            index = words.index("to") + 1
            if index < len(words) and words[index] == "savepoint":
                index += 1
            if index < len(words):
                matches = [i for i, name in enumerate(self._savepoints) if name == words[index]]
                if matches:
                    del self._savepoints[matches[-1] + 1 :]
        if self.in_transaction:
            if not had_transaction:
                self._rows_changed_at_transaction_start = before
        elif had_transaction:
            wrote = self._open_transaction_wrote()
            self._clear_transaction()
            if commits and wrote:
                change_signal.emit()
        elif self.total_changes != before:
            change_signal.emit()
        return result

    def _sqlite_execute(self, cursor: sqlite3.Cursor, sql: str, parameters: Any) -> sqlite3.Cursor:
        return sqlite3.Cursor.execute(cursor, sql, parameters)

    def _before_commit(self) -> None:
        if self._finalizing:
            raise RuntimeError("transaction participants cannot commit")
        self._finalizing = True
        try:
            finalize(self, self._participants)
        except BaseException:
            self._rollback_failed()
            raise
        finally:
            self._finalizing = False

    def _commit(self, *, announce: bool) -> None:
        self._check_transaction_control()
        had_transaction = self.in_transaction
        if had_transaction:
            self._before_commit()
        wrote = self._open_transaction_wrote()
        try:
            self._sqlite_commit()
        except BaseException as error:
            self._failed_commit(error)
            raise
        self._clear_transaction()
        if announce and had_transaction and wrote:
            change_signal.emit()

    def _sqlite_commit(self) -> None:
        super().commit()

    def commit(self) -> None:
        self._commit(announce=True)

    def rollback(self) -> None:
        self._check_transaction_control()
        self._rollback_failed()

    def _rollback_failed(self) -> None:
        try:
            super().rollback()
        finally:
            self._clear_transaction()

    def __enter__(self) -> Self:
        if self.in_transaction:
            self._context_sequence += 1
            name = f"managed_context_{self._context_sequence}"
            self.execute(f"SAVEPOINT {name}")
        else:
            name = None
            self.execute("BEGIN")
        self._contexts.append(name)
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Literal[False]:
        name = self._contexts.pop()
        if not self.in_transaction:
            return False
        if name is not None:
            if exc_type is not None:
                self.execute(f"ROLLBACK TO {name}")
            self.execute(f"RELEASE {name}")
        elif exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    def _clear_transaction(self) -> None:
        self._rows_changed_at_transaction_start = None
        self._savepoints.clear()
        self._savepoint_transaction = False

    def _open_transaction_wrote(self) -> bool:
        baseline = self._rows_changed_at_transaction_start
        return baseline is None or self.total_changes != baseline


@contextmanager
def transaction_control_guard(connection: ChangeSignallingConnection, /) -> Iterator[None]:
    connection._control_guard_depth += 1
    connection._refresh_authorizer()
    try:
        yield
    finally:
        connection._control_guard_depth -= 1
        connection._refresh_authorizer()


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
    if isinstance(conn, ChangeSignallingConnection):
        conn._commit(announce=False)
    else:
        conn.execute("COMMIT")


def connect(db_path: str, busy_timeout_ms: int = DB_BUSY_TIMEOUT_MS) -> sqlite3.Connection:
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

    from planner.database_modules import attach

    conn._participants = attach(conn)
    return conn


def create_schema(
    conn: sqlite3.Connection,
    *,
    startup_audit: Callable[[sqlite3.Connection], None] | None = None,
) -> None:
    """Bring the connected database to the current schema and seed default projects.

    Safe to call on every open: a new database is built, a current one is left alone.
    """
    # Seeding is the only thing here that knows a domain, and domains open connections
    # through this module — so these are imported where they are used, leaving this module
    # importable on its own.
    from planner.managed_skills import seed_packaged_skills, write_skill_home
    from planner.notifications import data as notifications_data
    from planner.projects import data as projects_data
    from planner.worker_types.configuration import load_worker_runtime_definitions
    from planner.worker_types.shipped import seed_chief_settings, seed_shipped_worker_types
    from planner.worker_types.store import worker_types_table_exists

    if conn.in_transaction:
        raise RuntimeError(
            "create_schema needs a connection with no transaction open, because migrations "
            "run on their own connection and would wait on this one's lock"
        )
    _upgrade_to_current_schema(_main_database_path(conn), _busy_timeout_ms(conn))
    # Migrations use another connection. Refresh this connection's cached schema
    # before its first write prepares triggers that migrations just removed.
    conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
    if startup_audit is not None:
        # Startup must mark invalid Tickets before any ordinary seed commit asks the
        # shared projections to interpret them. A new database needs its registry first.
        notification_schema_exists = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='notification_web_push_identity'"
            ).fetchone()
            is not None
        )
        with conn:
            projects_data.seed_default_projects(conn)
            if worker_types_table_exists(conn):
                seed_packaged_skills(conn)
                seed_shipped_worker_types(conn)
                seed_chief_settings(conn)
                notifications_data.seed_shipped_notification_preferences(conn)
                load_worker_runtime_definitions(conn)
            startup_audit(conn)
        if notification_schema_exists:
            notifications_data.get_or_create_web_push_identity(conn, 0)
        if worker_types_table_exists(conn):
            write_skill_home(conn, Path(_main_database_path(conn)).parent)
        return
    with conn:
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


@contextmanager
def schema_migration_transaction(
    db_path: str, busy_timeout_ms: int = DB_BUSY_TIMEOUT_MS
) -> Iterator[Connection]:
    """Hold the all-or-nothing schema transaction used by application startup.

    Explicit offline maintenance can prepare historical schema on this connection,
    after the original database archive. Nothing is upgraded merely by entry.
    """
    engine = _migration_engine(db_path, busy_timeout_ms)
    try:
        with engine.connect() as connection:
            with connection.begin():
                yield connection
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"migrations left foreign key violations: {violations!r}")
    finally:
        engine.dispose()


def upgrade_schema_connection(connection: Connection, revision: str) -> None:
    """Apply packaged revisions within the caller's existing transaction."""
    command.upgrade(_alembic_config(connection), revision)


def _upgrade_to_current_schema(db_path: str, busy_timeout_ms: int) -> None:
    """Run every migration the database has not seen yet, as one all-or-nothing step."""
    with schema_migration_transaction(db_path, busy_timeout_ms) as connection:
        upgrade_schema_connection(connection, "head")


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
        # Existing module triggers also run during historical source rewrites.
        # Attach their SQL functions before migrations, on this same connection.
        from planner.database_modules import attach

        attach(dbapi_connection)

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

