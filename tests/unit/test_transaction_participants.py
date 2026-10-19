"""Commit boundaries preserve source/participant atomicity and wake suppression."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import uvicorn

from planner.core import change_signal
from planner.core.db import ChangeSignallingConnection, commit_without_change_signal, connect
from planner.navigation.contracts import SCHEMA_VERSION


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[ChangeSignallingConnection]:
    conn = connect(str(tmp_path / "transaction.db"))
    assert isinstance(conn, ChangeSignallingConnection)
    conn.execute("CREATE TABLE source (value INTEGER)")
    conn.execute("CREATE TABLE journal (value INTEGER)")
    conn.execute("CREATE TABLE dirty (value INTEGER)")
    conn.execute(
        "CREATE TRIGGER capture AFTER INSERT ON source BEGIN "
        "INSERT INTO dirty VALUES (new.value); END"
    )

    def participant(db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO journal SELECT SUM(value) FROM dirty HAVING COUNT(*) > 0")
        db.execute("DELETE FROM dirty")

    conn._participants = (participant,)
    yield conn
    conn.close()


@pytest.mark.parametrize("route", ["method", "sql", "end", "cursor", "context", "quiet", "release"])
def test_every_commit_route_finalizes_once(
    connection: ChangeSignallingConnection, route: str
) -> None:
    conn = connection
    signal = Mock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(change_signal, "emit", signal)
        if route == "context":
            conn.__enter__()
        else:
            conn.execute('SAVEPOINT "outer space"' if route == "release" else "BEGIN")
        conn.executemany("INSERT INTO source VALUES (?)", [(1,), (2,)])
        conn.execute("SAVEPOINT inner")
        conn.cursor().execute("INSERT INTO source VALUES (99)")
        conn.execute("ROLLBACK TO inner")
        conn.execute("RELEASE inner")
        assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0
        if route == "method":
            conn.commit()
        elif route == "sql":
            conn.execute("/* boundary */COMMIT/* adjacent comment */;")
        elif route == "end":
            conn.execute("-- boundary\nEND TRANSACTION")
        elif route == "cursor":
            conn.cursor().execute("COMMIT")
        elif route == "context":
            conn.__exit__(None, None, None)
        elif route == "quiet":
            commit_without_change_signal(conn)
        else:
            conn.execute('RELEASE SAVEPOINT "outer space"')
        assert signal.call_count == (0 if route == "quiet" else 1)
    assert [row[0] for row in conn.execute("SELECT value FROM source")] == [1, 2]
    assert [row[0] for row in conn.execute("SELECT value FROM journal")] == [3]
    assert conn.execute("SELECT COUNT(*) FROM dirty").fetchone()[0] == 0


@pytest.mark.parametrize("route", ["method", "sql", "context", "quiet", "release"])
def test_participant_failure_rolls_back_everything(
    connection: ChangeSignallingConnection, route: str
) -> None:
    conn = connection

    def fail(db: sqlite3.Connection) -> None:
        db.execute("INSERT INTO journal VALUES (500)")
        raise ValueError("projection failed")

    conn._participants = (fail,)
    if route == "context":
        conn.__enter__()
    else:
        conn.execute("SAVEPOINT outer" if route == "release" else "BEGIN")
    conn.execute("INSERT INTO source VALUES (1)")
    with pytest.raises(ValueError, match="projection failed"):
        if route == "method":
            conn.commit()
        elif route == "context":
            conn.__exit__(None, None, None)
        elif route == "quiet":
            commit_without_change_signal(conn)
        else:
            conn.execute("RELEASE outer" if route == "release" else "COMMIT")
    assert not conn.in_transaction
    for table in ("source", "journal", "dirty"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("route", ["method", "sql", "cursor", "end", "quiet", "release"])
def test_failed_sqlite_commit_rolls_back_finalization(
    connection: ChangeSignallingConnection,
    route: str,
) -> None:
    conn = connection
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE child (id INTEGER REFERENCES parent DEFERRABLE INITIALLY DEFERRED)")
    conn.execute("SAVEPOINT outer" if route == "release" else "BEGIN")
    conn.execute("INSERT INTO source VALUES (1)")
    conn.execute("INSERT INTO child VALUES (999)")
    with pytest.raises(sqlite3.IntegrityError):
        if route == "method":
            conn.commit()
        elif route == "cursor":
            conn.cursor().execute("COMMIT")
        elif route == "quiet":
            commit_without_change_signal(conn)
        else:
            conn.execute({"sql": "COMMIT", "end": "END", "release": "RELEASE outer"}[route])
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0


def test_dollar_identifiers_do_not_hide_outer_release(
    connection: ChangeSignallingConnection,
) -> None:
    connection.execute("SAVEPOINT outer")
    connection.execute("SAVEPOINT outer$child")
    connection.execute("INSERT INTO source VALUES (0)")
    connection.execute("RELEASE outer")
    assert not connection.in_transaction
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [0]


def test_bom_commit_and_unicode_savepoints(connection: ChangeSignallingConnection) -> None:
    connection.execute('SAVEPOINT "ß"')
    connection.execute('SAVEPOINT "ss"')
    connection.execute("INSERT INTO source VALUES (1)")
    connection.execute('RELEASE "ß"')
    assert not connection.in_transaction
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [1]
    connection.execute("BEGIN")
    connection.execute("INSERT INTO source VALUES (2)")
    connection.execute("\ufeffCOMMIT")
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [1, 2]


def test_empty_commit_and_context_rollback_stay_silent(
    connection: ChangeSignallingConnection,
) -> None:
    signal = Mock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(change_signal, "emit", signal)
        connection.execute("BEGIN")
        connection.commit()
        with pytest.raises(ValueError):
            with connection:
                connection.execute("INSERT INTO source VALUES (1)")
                raise ValueError("abort")
        assert signal.call_count == 0
    assert connection.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0


def test_context_groups_standalone_writes_and_preserves_outer_caller(
    connection: ChangeSignallingConnection,
) -> None:
    with pytest.raises(ValueError):
        with connection:
            connection.execute("INSERT INTO source VALUES (1)")
            connection.execute("INSERT INTO source VALUES (2)")
            raise ValueError("abort both")
    assert connection.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    connection.execute("BEGIN")
    connection.execute("INSERT INTO source VALUES (3)")
    with pytest.raises(ValueError):
        with connection:
            connection.execute("INSERT INTO source VALUES (4)")
            raise ValueError("abort inner")
    with connection:
        connection.execute("INSERT INTO source VALUES (5)")
    assert connection.in_transaction
    assert connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0
    connection.commit()
    assert [row[0] for row in connection.execute("SELECT value FROM source")] == [3, 5]
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [8]


def test_automatic_sqlite_rollback_clears_control_state(
    connection: ChangeSignallingConnection,
) -> None:
    connection.execute("CREATE TABLE unique_values (value INTEGER UNIQUE)")
    connection.execute("SAVEPOINT outer")
    connection.execute("INSERT INTO unique_values VALUES (1)")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT OR ROLLBACK INTO unique_values VALUES (1)")
    assert not connection.in_transaction
    connection.execute("BEGIN")
    connection.execute("SAVEPOINT outer")
    connection.execute("INSERT INTO source VALUES (4)")
    connection.execute("RELEASE outer")
    assert connection.in_transaction
    assert connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0
    connection.commit()
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [4]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO source VALUES (4)",
        "/* first */INSERT/* second */INTO source VALUES (4)",
        "-- first\nWITH value(v) AS (SELECT 4) INSERT INTO source SELECT v FROM value",
        "REPLACE INTO source VALUES (4)",
    ],
)
def test_standalone_operations_finalize_before_return(
    connection: ChangeSignallingConnection, sql: str
) -> None:
    connection.cursor().execute(sql)
    assert not connection.in_transaction
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [4]


def test_standalone_bulk_is_one_atomic_unit(connection: ChangeSignallingConnection) -> None:
    connection.executemany("INSERT INTO source VALUES (?)", [(1,), (2,), (3,)])
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [6]
    with pytest.raises(sqlite3.ProgrammingError):
        connection.cursor().executemany("INSERT INTO source VALUES (?)", [(4,), (5, 6)])
    assert [row[0] for row in connection.execute("SELECT value FROM source")] == [1, 2, 3]
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [6]


def test_script_routes_initial_and_embedded_commits(connection: ChangeSignallingConnection) -> None:
    connection.execute("BEGIN")
    connection.execute("INSERT INTO source VALUES (1)")
    connection.cursor().executescript("""
        -- A trigger body contains semicolons that cannot split statements.
        CREATE TABLE indirect (value INTEGER);
        CREATE TRIGGER indirect_capture AFTER INSERT ON indirect BEGIN
            INSERT INTO source VALUES (new.value);
            INSERT INTO source VALUES (10);
        END;
        BEGIN;
        INSERT INTO indirect VALUES (2);
        END;
        /* tail */
    """)
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [1, 12]


def test_standalone_participant_failure_preserves_no_source(
    connection: ChangeSignallingConnection,
) -> None:
    def fail(db: sqlite3.Connection) -> None:
        raise ValueError("cannot finalize")

    connection._participants = (fail,)
    with pytest.raises(ValueError, match="cannot finalize"):
        connection.execute("WITH value(v) AS (SELECT 2) INSERT INTO source SELECT v FROM value")
    assert not connection.in_transaction
    assert connection.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0


def test_duplicate_savepoint_names_preserve_outer_commit(
    connection: ChangeSignallingConnection,
) -> None:
    conn = connection
    conn.execute('SAVEPOINT "repeat"')
    conn.execute("INSERT INTO source VALUES (1)")
    conn.execute('SAVEPOINT "repeat"')
    conn.execute("INSERT INTO source VALUES (2)")
    conn.execute('RELEASE "repeat"')
    assert conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0
    conn.execute('RELEASE "repeat"')
    assert [row[0] for row in conn.execute("SELECT value FROM journal")] == [3]


@pytest.mark.parametrize("operation", ["commit", "rollback", "sql"])
def test_participant_cannot_control_transaction(
    connection: ChangeSignallingConnection, operation: str
) -> None:
    def invalid(db: sqlite3.Connection) -> None:
        if operation == "commit":
            db.commit()
        elif operation == "rollback":
            db.rollback()
        else:
            db.execute("SAVEPOINT forbidden")

    connection._participants = (invalid,)
    connection.execute("BEGIN")
    connection.execute("INSERT INTO source VALUES (1)")
    with pytest.raises(RuntimeError, match="transaction participants cannot"):
        connection.commit()
    assert not connection.in_transaction
    assert connection.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0


def test_returning_requires_explicit_transaction_and_consumed_rows(
    connection: ChangeSignallingConnection,
) -> None:
    with pytest.raises(sqlite3.OperationalError, match="SQL statements in progress"):
        connection.execute("INSERT INTO source VALUES (1) RETURNING value")
    assert connection.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0
    connection.execute("BEGIN")
    result = connection.execute("INSERT INTO source VALUES (2) RETURNING value").fetchall()
    connection.commit()
    assert result[0][0] == 2
    assert [row[0] for row in connection.execute("SELECT value FROM journal")] == [2]


def test_application_bootstrap_does_not_enclose_schema_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.server_lifecycle import application

    db_path = tmp_path / "bootstrap.db"
    config = SimpleNamespace(
        db_path=str(db_path),
        db_busy_timeout_ms=5000,
        logs_dir=str(tmp_path / "logs"),
        port=0,
    )
    monkeypatch.chdir(Path.cwd())
    monkeypatch.delenv("PLAN_SERVER_LISTENER_FD", raising=False)
    monkeypatch.setattr(application, "load_config", Mock(return_value=config))
    monkeypatch.setattr(application, "build_clock", Mock())
    monkeypatch.setattr(application, "create_app", Mock())
    server = Mock()
    monkeypatch.setattr(application, "ChangeStreamClosingServer", Mock(return_value=server))
    monkeypatch.setattr(uvicorn, "Config", Mock())
    application.run_application_process()
    server.run.assert_called_once()
    db = connect(str(db_path))
    try:
        assert (
            db.execute("SELECT schema_version FROM navigation_meta").fetchone()[0] == SCHEMA_VERSION
        )
    finally:
        db.close()
