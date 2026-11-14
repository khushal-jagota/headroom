"""Scoped loans preserve transaction boundaries while sharing a bounded set of handles."""

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from planner.core import change_signal
from planner.core import runtime_connections as connections_module
from planner.core.db import ChangeSignallingConnection, commit_without_change_signal, connect
from planner.core.runtime_connections import (
    RuntimeConnections,
    reuse_connections,
    runtime_connection,
)


def test_exhausted_pool_does_not_queue_event_loop_read_behind_writers(tmp_path: Path) -> None:
    path = str(tmp_path / "pool.db")
    pool = RuntimeConnections(path, capacity=6)
    writer = connect(path)
    writer.execute("CREATE TABLE source(value INTEGER)")
    writer.execute("INSERT INTO source VALUES(1)")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE source SET value=2")
    entered = threading.Barrier(pool.capacity + 1)
    release = threading.Event()
    read_finished = threading.Event()

    def hold() -> int:
        with pool.connection() as conn:
            assert conn.execute("SELECT value FROM source").fetchone()[0] == 1
            entered.wait(timeout=5)
            assert release.wait(5)
            return id(conn)

    def watchdog() -> None:
        # A broken blocking checkout must terminate the test, but releasing the
        # holders before the read finishes is always an assertion failure below.
        read_finished.wait(5)
        release.set()

    async def read() -> None:
        assert asyncio.get_running_loop() is not None
        with pool.connection() as overflow:
            assert overflow.execute("SELECT value FROM source").fetchone()[0] == 1
            assert pool.connections_open == pool.capacity
        with pytest.raises(sqlite3.ProgrammingError):
            overflow.execute("SELECT 1")
        assert not release.is_set()
        assert all(not holder.done() for holder in holders)
        read_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=pool.capacity + 1) as executor:
            holders = [executor.submit(hold) for _ in range(pool.capacity)]
            entered.wait(timeout=5)
            safety = executor.submit(watchdog)
            try:
                asyncio.run(read())
            finally:
                release.set()
                read_finished.set()
            retained_ids = {holder.result(timeout=5) for holder in holders}
            safety.result(timeout=5)
            assert len(retained_ids) == pool.capacity

            def borrow_again() -> int:
                with pool.connection() as conn:
                    return id(conn)

            assert executor.submit(borrow_again).result(timeout=5) in retained_ids
    finally:
        release.set()
        writer.close()
        pool.close()
    assert pool.connections_open == 0


def test_cleanup_failures_preserve_borrower_error_and_release_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = RuntimeConnections(str(tmp_path / "pool.db"), capacity=1)
    primary = ValueError("borrower failed")
    rollback_error = RuntimeError("rollback failed")
    close_error = RuntimeError("close failed")
    original_close = connections_module._close_connection
    notified = Mock(wraps=pool._condition.notify_all)

    def failing_close(conn: ChangeSignallingConnection) -> None:
        original_close(conn)
        raise close_error

    with monkeypatch.context() as patch:
        patch.setattr(connections_module, "_close_connection", failing_close)
        patch.setattr(pool._condition, "notify_all", notified)
        with pytest.raises(ValueError) as raised:
            with pool.connection() as conn:
                patch.setattr(conn, "rollback", Mock(side_effect=rollback_error))
                raise primary
        assert raised.value is primary
        assert any("rollback failed" in note for note in primary.__notes__)
        assert any("close failed" in note for note in primary.__notes__)
        notified.assert_called_once_with()
        assert pool.connections_open == 0
    with pool.connection() as replacement:
        assert replacement is not conn
        assert replacement.execute("SELECT 1").fetchone()[0] == 1
    with monkeypatch.context() as patch:
        patch.setattr(connections_module, "_close_connection", failing_close)
        patch.setattr(pool._condition, "notify_all", notified)
        with pytest.raises(RuntimeError) as closed:
            pool.close()
        assert closed.value is close_error
        assert pool.connections_open == 0
        assert notified.call_count == 2


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
