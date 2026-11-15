"""Commit boundaries preserve source/participant atomicity and wake suppression."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest

from planner.core import change_signal
from planner.core.db import ChangeSignallingConnection, commit_without_change_signal, connect


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
