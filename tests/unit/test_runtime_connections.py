"""Scoped loans preserve transaction boundaries while sharing a bounded set of handles."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from planner.core import change_signal
from planner.core.db import ChangeSignallingConnection, commit_without_change_signal
from planner.core.runtime_connections import (
    RuntimeConnections,
    reuse_connections,
    runtime_connection,
)


def test_exclusive_bound_and_cross_thread_reuse(tmp_path: Path) -> None:
    pool = RuntimeConnections(str(tmp_path / "pool.db"), capacity=1)
    entered = threading.Event()
    contender_started = threading.Event()
    release = threading.Event()

    def first() -> int:
        with pool.connection() as conn:
            entered.set()
            assert release.wait(5)
            return id(conn)

    def second() -> int:
        contender_started.set()
        with pool.connection() as conn:
            return id(conn)

    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(first)
        assert entered.wait(5)
        two = executor.submit(second)
        assert contender_started.wait(5)
        with pytest.raises(TimeoutError):
            two.result(timeout=0.05)
        assert pool.connections_open == 1
        release.set()
        assert one.result(5) == two.result(5)
    pool.close()
    assert pool.connections_open == 0


def test_rollback_and_participants_keep_existing_commit_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal = Mock()
    monkeypatch.setattr(change_signal, "emit", signal)
    pool = RuntimeConnections(str(tmp_path / "pool.db"), capacity=1)
    with pool.connection() as conn:
        assert isinstance(conn, ChangeSignallingConnection)
        conn.execute("CREATE TABLE source(value INTEGER)")
        conn.execute("CREATE TABLE journal(value INTEGER)")

        def participant(db: sqlite3.Connection) -> None:
            db.execute("INSERT INTO journal VALUES(1)")

        conn._participants = (participant,)
        signal.reset_mock()
        conn.execute("BEGIN")
        conn.execute("INSERT INTO source VALUES(1)")
        commit_without_change_signal(conn)
        signal.assert_not_called()
        conn.execute("BEGIN")
        conn.execute("INSERT INTO source VALUES(2)")
    with pool.connection() as same:
        assert same is conn
        assert same.execute("SELECT value FROM source").fetchall()[0][0] == 1
        assert same.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1
        with same:
            same.execute("INSERT INTO source VALUES(3)")
        signal.assert_called_once_with()
    pool.close()


def test_scope_shutdown_and_failed_loans_retire_handles(tmp_path: Path) -> None:
    path = str(tmp_path / "pool.db")
    with reuse_connections(path) as pool:
        with runtime_connection(path) as first:
            with pytest.raises(RuntimeError, match="nested"):
                with runtime_connection(path):
                    pass
        with pytest.raises(ValueError):
            with runtime_connection(path) as failed:
                assert failed is first
                raise ValueError("discard")
        with runtime_connection(path) as replacement:
            assert replacement is not failed
        assert pool.connections_open == 1
    assert pool.connections_open == 0
    with pytest.raises(sqlite3.ProgrammingError):
        replacement.execute("SELECT 1")
    with runtime_connection(path) as fresh:
        assert fresh.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError):
        fresh.execute("SELECT 1")


def test_shutdown_retires_outstanding_loan_on_return(tmp_path: Path) -> None:
    pool = RuntimeConnections(str(tmp_path / "pool.db"))
    with pool.connection() as conn:
        pool.close()
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert pool.connections_open == 0
    with pytest.raises(RuntimeError, match="closed"):
        with pool.connection():
            pass
