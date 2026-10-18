"""Injected guards prove retained admission mechanics, never Signal policy."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from planner.core import change_signal
from planner.core.commit_observers import CommitObserver
from planner.core.db import (
    ChangeSignallingConnection,
    bind_transaction_admission,
    commit_without_change_signal,
    register_transaction_guard,
    schema_migration_transaction,
    transaction_control_guard,
    transaction_savepoint_depth,
)


class Lease:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.reject_savepoint = False
        self.fail: str | None = None

    def check_savepoint(self, action: str) -> None:
        self.events.append(("savepoint", action))
        if self.reject_savepoint:
            raise ValueError("candidate forbids savepoints")

    def settle(self, outcome: str, error: BaseException | None) -> None:
        self.events.append(("settle", outcome, type(error).__name__ if error else None))
        if self.fail == "settle":
            raise ValueError("settle failed")

    def invalidate(self, error: BaseException) -> None:
        self.events.append(("invalidate", str(error)))
        if self.fail == "invalidate":
            raise ValueError("invalidate failed")

    def release(self) -> None:
        self.events.append("release")
        if self.fail == "release":
            raise ValueError("release failed")


class Factory:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.leases: list[Lease] = []

    def enter(self, connection: sqlite3.Connection, admission: object | None) -> Lease:
        assert not connection.in_transaction
        self.events.append(("enter", admission))
        lease = Lease(self.events)
        self.leases.append(lease)
        return lease


def open_database(path: Path) -> ChangeSignallingConnection:
    conn = sqlite3.connect(path, isolation_level=None, factory=ChangeSignallingConnection)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE IF NOT EXISTS source (value INTEGER UNIQUE)")
    return conn


@pytest.mark.parametrize(
    "route",
    [
        "method",
        "sql",
        "end",
        "cursor",
        "quiet",
        "context",
        "release",
        "standalone",
        "bulk",
        "script",
    ],
)
def test_registered_guard_covers_all_retained_outer_paths(tmp_path: Path, route: str) -> None:
    path = tmp_path / "guard.db"
    conn = open_database(path)  # An idle connection predates registration.
    factory = Factory()
    registration = register_transaction_guard(str(path), factory)
    token = object()
    conn._participants = (lambda db: factory.events.append("finalize"),)
    conn._commit_observers = (
        CommitObserver(lambda db, results: factory.events.append("observe"), lambda results: None),
    )
    with bind_transaction_admission(conn, token):
        if route == "standalone":
            conn.execute("INSERT INTO source VALUES (1)")
        elif route == "bulk":
            conn.executemany("INSERT INTO source VALUES (?)", [(1,), (2,)])
        elif route == "script":
            conn.executescript("BEGIN; INSERT INTO source VALUES (1); COMMIT;")
        else:
            conn.execute("SAVEPOINT outer" if route == "release" else "BEGIN")
            conn.execute("INSERT INTO source VALUES (1)")
            if route == "method":
                conn.commit()
            elif route == "quiet":
                commit_without_change_signal(conn)
            elif route == "context":
                with conn:
                    conn.execute("INSERT INTO source VALUES (2)")
                conn.commit()
            elif route == "cursor":
                conn.cursor().execute("COMMIT")
            else:
                conn.execute({"sql": "COMMIT", "end": "END", "release": "RELEASE outer"}[route])
    assert factory.events[0] == ("enter", token)
    assert factory.events[-4:] == ["finalize", ("settle", "committed", None), "release", "observe"]
    assert len(factory.leases) == 1
    registration.close()
    with pytest.raises(RuntimeError, match="unavailable"):
        conn.execute("BEGIN")
    conn.close()


def test_active_registration_and_gate_wait_are_atomic(tmp_path: Path) -> None:
    path = tmp_path / "registration.db"
    conn = open_database(path)
    conn.execute("BEGIN")
    factory = Factory()
    with pytest.raises(RuntimeError, match="active"):
        register_transaction_guard(str(path), factory)
    conn.rollback()
    entered = threading.Event()
    proceed = threading.Event()

    class BlockingFactory(Factory):
        def enter(self, connection: sqlite3.Connection, admission: object | None) -> Lease:
            entered.set()
            assert proceed.wait(3)
            return super().enter(connection, admission)

    blocking = BlockingFactory()
    registration = register_transaction_guard(str(path), blocking)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            other = open_database(path)
            other.execute("BEGIN")
            other.rollback()
            other.close()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(3)
    # This registry action must finish while factory.enter waits for its gate.
    with pytest.raises(RuntimeError, match="active"):
        register_transaction_guard(str(path), factory)
    proceed.set()
    thread.join(3)
    assert not thread.is_alive() and not errors
    registration.close()
    conn.close()


def test_nested_savepoints_keep_guard_and_candidate_can_refuse(tmp_path: Path) -> None:
    path = tmp_path / "nested.db"
    conn = open_database(path)
    factory = Factory()
    registration = register_transaction_guard(str(path), factory)
    conn.execute("BEGIN")
    with pytest.raises(RuntimeError, match="active transaction"):
        registration.close()
    conn.execute("SAVEPOINT first")
    conn.execute("INSERT INTO source VALUES (1)")
    conn.execute("SAVEPOINT second")
    assert transaction_savepoint_depth(conn) == 2
    conn.execute("ROLLBACK TO first")
    assert transaction_savepoint_depth(conn) == 1
    conn.execute("RELEASE first")
    assert conn.in_transaction
    factory.leases[-1].reject_savepoint = True
    with pytest.raises(ValueError, match="candidate"):
        conn.execute("SAVEPOINT denied")
    assert transaction_savepoint_depth(conn) == 0
    conn.commit()
    assert factory.events.count("release") == 1
    assert conn.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    registration.close()
    conn.close()


@pytest.mark.parametrize(
    "route",
    [
        "rollback",
        "sql_rollback",
        "close",
        "automatic",
        "begin_failure",
        "savepoint_failure",
        "participant_failure",
        "commit_failure",
    ],
)
def test_known_rollback_settles_once(tmp_path: Path, route: str) -> None:
    path = tmp_path / "rollback.db"
    conn = open_database(path)
    factory = Factory()
    registration = register_transaction_guard(str(path), factory)
    if route in {"begin_failure", "savepoint_failure"}:
        conn.set_authorizer(
            lambda action, *args: (
                sqlite3.SQLITE_DENY
                if action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}
                else sqlite3.SQLITE_OK
            )
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("BEGIN" if route == "begin_failure" else "SAVEPOINT denied")
    else:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO source VALUES (1)")
        if route == "rollback":
            conn.rollback()
        elif route == "sql_rollback":
            conn.execute("ROLLBACK")
        elif route == "close":
            conn.close()
        elif route == "automatic":
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT OR ROLLBACK INTO source VALUES (1)")
        elif route == "participant_failure":

            def fail(db: sqlite3.Connection) -> None:
                raise ValueError("participant failed")

            conn._participants = (fail,)
            with pytest.raises(ValueError):
                conn.commit()
        else:
            conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE child (id INTEGER REFERENCES parent DEFERRABLE INITIALLY DEFERRED)"
            )
            conn.execute("INSERT INTO child VALUES (2)")
            with pytest.raises(sqlite3.IntegrityError):
                conn.commit()
    settlements = [
        event for event in factory.events if isinstance(event, tuple) and event[0] == "settle"
    ]
    assert len(settlements) == 1 and settlements[0][1] == "rolled_back"
    assert factory.events.count("release") == 1
    registration.close()
    conn.close()


@pytest.mark.parametrize("failure", ["settle", "release", "invalidate"])
def test_settlement_failure_poison_precedes_notifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = tmp_path / "poison.db"
    conn = open_database(path)
    factory = Factory()
    registration = register_transaction_guard(str(path), factory)
    observer = Mock()
    signal = Mock()
    monkeypatch.setattr(change_signal, "emit", signal)
    conn._commit_observers = (observer,)
    conn.execute("BEGIN")
    conn.execute("INSERT INTO source VALUES (1)")
    lease = factory.leases[-1]
    lease.fail = "settle" if failure == "invalidate" else failure
    if failure == "invalidate":

        def fail_invalidation(error: BaseException) -> None:
            raise ValueError("invalidate failed")

        monkeypatch.setattr(lease, "invalidate", fail_invalidation)
    with pytest.raises(ValueError, match="settle" if failure == "invalidate" else failure):
        conn.commit()
    assert factory.events.count("release") == 1
    observer.assert_not_called()
    signal.assert_not_called()
    with pytest.raises(RuntimeError, match="unavailable"):
        conn.execute("BEGIN")
    registration.close()
    fresh = register_transaction_guard(str(path), Factory())
    conn.execute("BEGIN")
    conn.rollback()
    fresh.close()
    conn.close()


def test_uncertain_commit_is_not_reclassified_or_observed(tmp_path: Path) -> None:
    class FailedResult(ChangeSignallingConnection):
        def _sqlite_commit(self) -> None:
            super()._sqlite_commit()
            raise OSError("lost commit result")

    path = tmp_path / "uncertain.db"
    conn = sqlite3.connect(path, factory=FailedResult, isolation_level=None)
    conn.execute("CREATE TABLE source (value INTEGER)")
    factory = Factory()
    registration = register_transaction_guard(str(path), factory)
    observer = Mock()
    conn._commit_observers = (observer,)
    conn.execute("BEGIN")
    conn.execute("INSERT INTO source VALUES (1)")
    with pytest.raises(OSError, match="lost commit"):
        conn.commit()
    assert ("settle", "uncertain", "OSError") in factory.events
    assert factory.events.count("release") == 1
    observer.assert_not_called()
    registration.close()
    conn.close()


@pytest.mark.parametrize(
    "operation", ["commit", "rollback", "close", "authorizer", "sql", "cached"]
)
def test_nested_control_guards_preserve_outer_authority(tmp_path: Path, operation: str) -> None:
    conn = open_database(tmp_path / "authorizer.db")
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT cached")
    conn.execute("RELEASE cached")
    with transaction_control_guard(conn):
        with transaction_control_guard(conn):
            conn.execute("SELECT 1")
        with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
            if operation == "authorizer":
                conn.set_authorizer(None)
            elif operation == "sql":
                conn.execute("COMMIT")
            elif operation == "cached":
                # Even native cached SQL sees the current dispatcher.
                sqlite3.Connection.execute(conn, "SAVEPOINT cached")
            else:
                getattr(conn, operation)()
    assert conn.in_transaction
    conn.rollback()
    conn.close()


def test_raw_migration_admission_observes_actual_result(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    factory = Factory()
    registration = register_transaction_guard(str(path), factory)
    token = object()
    with schema_migration_transaction(str(path), admission=token) as conn:
        conn.exec_driver_sql("CREATE TABLE isolated (value INTEGER)")
        conn.exec_driver_sql("INSERT INTO isolated VALUES (1)")
    assert factory.events == [("enter", token), ("settle", "committed", None), "release"]
    with pytest.raises(ValueError, match="abort"):
        with schema_migration_transaction(str(path), admission=token) as conn:
            conn.exec_driver_sql("INSERT INTO isolated VALUES (2)")
            raise ValueError("abort")
    assert factory.events[-2:] == [("settle", "rolled_back", "ValueError"), "release"]
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT * FROM isolated").fetchall() == [(1,)]
    registration.close()


def test_waiting_admission_cannot_cross_a_poison_fence(tmp_path: Path) -> None:
    path = tmp_path / "waiting.db"
    first = open_database(path)
    waiting = threading.Event()
    proceed = threading.Event()

    class WaitingFactory(Factory):
        def enter(self, connection: sqlite3.Connection, admission: object | None) -> Lease:
            if connection is not first:
                waiting.set()
                assert proceed.wait(3)
            return super().enter(connection, admission)

    factory = WaitingFactory()
    registration = register_transaction_guard(str(path), factory)
    first.execute("BEGIN")
    factory.leases[0].fail = "settle"
    errors: list[BaseException] = []

    def worker() -> None:
        other = open_database(path)
        try:
            other.execute("BEGIN")
        except BaseException as error:
            errors.append(error)
        finally:
            other.close()

    thread = threading.Thread(target=worker)
    thread.start()
    assert waiting.wait(3)
    with pytest.raises(ValueError, match="settle failed"):
        first.rollback()
    proceed.set()
    thread.join(3)
    assert not thread.is_alive()
    assert len(errors) == 1 and "became unavailable" in str(errors[0])
    assert len(factory.leases) == 2
    assert factory.events.count("release") == 2
    assert factory.events[-2] == ("settle", "rolled_back", "RuntimeError")
    registration.close()
    first.close()


def test_schema_admission_covers_migrations_and_initialization(tmp_path: Path) -> None:
    from planner.core.db import connect, create_schema

    path = tmp_path / "complete-schema.db"
    conn = connect(str(path))
    token = object()

    class ScopedFactory(Factory):
        def enter(self, connection: sqlite3.Connection, admission: object | None) -> Lease:
            assert admission is token
            return super().enter(connection, admission)

    factory = ScopedFactory()
    registration = register_transaction_guard(str(path), factory)
    create_schema(conn, migration_admission=token)
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] > 0
    assert len(factory.leases) > 1
    assert all(
        event[1] == "committed"
        for event in factory.events
        if isinstance(event, tuple) and event[0] == "settle"
    )
    registration.close()
    conn.close()


def test_raw_migration_uncertainty_survives_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.core.db import _MigrationConnection

    path = tmp_path / "uncertain-migration.db"
    factory = Factory()
    registration = register_transaction_guard(str(path), factory)

    def lost_result(connection: _MigrationConnection) -> None:
        sqlite3.Connection.commit(connection)
        raise OSError("lost migration commit result")

    monkeypatch.setattr(_MigrationConnection, "_sqlite_commit", lost_result)
    with pytest.raises(OSError, match="lost migration"):
        with schema_migration_transaction(str(path)) as conn:
            conn.exec_driver_sql("CREATE TABLE committed (value INTEGER)")
    assert factory.events[-2:] == [("settle", "uncertain", "OSError"), "release"]
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT COUNT(*) FROM committed").fetchone()[0] == 0
    registration.close()


def test_stale_registration_cannot_close_reused_factory(tmp_path: Path) -> None:
    path = tmp_path / "registration-identity.db"
    conn = open_database(path)
    factory = Factory()
    old = register_transaction_guard(str(path), factory)
    old.close()
    current = register_transaction_guard(str(path), factory)
    old.close()
    conn.execute("BEGIN")
    conn.rollback()
    assert factory.events[-2:] == [("settle", "rolled_back", None), "release"]
    current.close()
    conn.close()


def test_raw_migration_failed_commit_requires_successful_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.core.db import _MigrationConnection

    path = tmp_path / "failed-migration.db"
    factory = Factory()
    registration = register_transaction_guard(str(path), factory)
    original = _MigrationConnection._sqlite_commit

    def fail_before_commit(connection: _MigrationConnection) -> None:
        assert connection.in_transaction
        raise OSError("migration commit rejected")

    monkeypatch.setattr(_MigrationConnection, "_sqlite_commit", fail_before_commit)
    with pytest.raises(OSError, match="commit rejected"):
        with schema_migration_transaction(str(path)) as conn:
            conn.exec_driver_sql("CREATE TABLE discarded (value INTEGER)")
    assert factory.events[-2:] == [("settle", "rolled_back", "OSError"), "release"]
    monkeypatch.setattr(_MigrationConnection, "_sqlite_commit", original)
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT name FROM sqlite_master WHERE name='discarded'").fetchall() == []
    registration.close()
