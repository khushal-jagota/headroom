"""Command witnesses prove inclusion, independent of revision arithmetic."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from planner.core.commit_capture import register_capture_task, run_in_capture_thread
from planner.core.commit_observers import CommitObserver
from planner.core.db import ChangeSignallingConnection, commit_without_change_signal, connect
from planner.navigation import proof


def fixture_database(path: Path) -> ChangeSignallingConnection:
    conn = connect(str(path))
    assert isinstance(conn, ChangeSignallingConnection)
    conn._participants = ()
    for sql in (
        "CREATE TABLE navigation_meta(singleton INTEGER,scope TEXT,schema_version INTEGER,"
        "epoch TEXT,revision INTEGER)",
        "INSERT INTO navigation_meta VALUES(1,'workspace',2,'epoch-one',0)",
        "CREATE TABLE navigation_dirty(kind TEXT,id TEXT)",
        "CREATE TABLE navigation_commit_witnesses(sequence INTEGER PRIMARY KEY AUTOINCREMENT,"
        "identity TEXT NOT NULL UNIQUE)",
        "CREATE TABLE navigation_proof_identity(singleton INTEGER,secret TEXT)",
        "INSERT INTO navigation_proof_identity VALUES(1,'synthetic-secret')",
        "CREATE TABLE source(value INTEGER UNIQUE)",
        "CREATE TRIGGER dirty AFTER INSERT ON source BEGIN "
        "INSERT INTO navigation_dirty VALUES('source','one'); END",
    ):
        conn.execute(sql)
    install(conn)
    return conn


def install(conn: ChangeSignallingConnection) -> None:
    def finalize(db: sqlite3.Connection) -> None:
        if db.execute("SELECT 1 FROM navigation_dirty LIMIT 1").fetchone():
            db.execute("UPDATE navigation_meta SET revision=revision+1")
            db.execute("DELETE FROM navigation_dirty")

    conn._participants = (proof.prepare_witness, finalize)


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[ChangeSignallingConnection]:
    conn = fixture_database(tmp_path / "fixture.db")
    yield conn
    conn.close()


def certify(conn: sqlite3.Connection, fence: object) -> str:
    conn.execute("BEGIN")
    try:
        return proof.certify(conn, fence)["status"]
    finally:
        conn.rollback()


@pytest.mark.parametrize("route", ["method", "sql", "end", "context", "quiet", "release", "bulk"])
def test_successful_outer_commit_is_captured_once(
    connection: ChangeSignallingConnection, route: str
) -> None:
    capture = proof.begin_capture()
    if route == "bulk":
        connection.executemany("INSERT INTO source VALUES(?)", [(1,), (2,)])
    else:
        connection.execute("SAVEPOINT outer" if route == "release" else "BEGIN")
        connection.execute("INSERT INTO source VALUES(1)")
        assert capture.witnesses == []
        if route == "method":
            connection.commit()
        elif route == "quiet":
            commit_without_change_signal(connection)
        elif route == "context":
            with connection:
                connection.execute("INSERT INTO source VALUES(2)")
            assert capture.witnesses == []
            connection.commit()
        else:
            connection.execute({"sql": "COMMIT", "end": "END", "release": "RELEASE outer"}[route])
    proof.end_capture(capture)
    assert len(capture.witnesses) == 1
    fence = proof.response_fence(connection, capture)
    assert certify(connection, json.loads(json.dumps(fence))) == "included"


def test_rollback_and_failed_outer_commit_capture_nothing(
    connection: ChangeSignallingConnection,
) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE child(id INTEGER REFERENCES parent DEFERRABLE INITIALLY DEFERRED)"
    )
    with proof.capture_command_commits() as capture:
        connection.execute("BEGIN")
        connection.execute("INSERT INTO source VALUES(1)")
        connection.rollback()
        connection.execute("BEGIN")
        connection.execute("INSERT INTO source VALUES(2)")
        connection.execute("INSERT INTO child VALUES(999)")
        with pytest.raises(sqlite3.IntegrityError):
            connection.commit()
    assert capture.witnesses == []
    assert connection.execute("SELECT count(*) FROM navigation_commit_witnesses").fetchone()[0] == 0


def test_savepoint_rollback_has_no_witness(connection: ChangeSignallingConnection) -> None:
    with proof.capture_command_commits() as capture:
        connection.execute("BEGIN")
        connection.execute("SAVEPOINT inner")
        connection.execute("INSERT INTO source VALUES(1)")
        connection.execute("ROLLBACK TO inner")
        connection.execute("RELEASE inner")
        connection.commit()
    assert capture.witnesses == []
    assert certify(connection, proof.response_fence(connection, capture)) == "no_op"


def test_overflow_unknown_does_not_certify_subset(connection: ChangeSignallingConnection) -> None:
    with proof.capture_command_commits(max_witnesses=1) as capture:
        connection.execute("INSERT INTO source VALUES(1)")
        connection.execute("INSERT INTO source VALUES(2)")
    assert len(capture.witnesses) == 1
    assert certify(connection, proof.response_fence(connection, capture)) == "unknown"


def test_observer_failure_preserves_domain_success(connection: ChangeSignallingConnection) -> None:
    def fail(conn: sqlite3.Connection, results: tuple[object, ...]) -> None:
        raise RuntimeError("capture failure")

    connection._commit_observers = (CommitObserver(fail, proof.COMMIT_OBSERVER.failed),)
    with proof.capture_command_commits() as capture:
        connection.execute("INSERT INTO source VALUES(1)")
    assert connection.execute("SELECT value FROM source").fetchone()[0] == 1
    assert not capture.complete
    assert certify(connection, proof.response_fence(connection, capture)) == "unknown"


def test_fence_survives_new_epoch_but_not_missing_witness_or_forgery(
    connection: ChangeSignallingConnection,
) -> None:
    with proof.capture_command_commits() as capture:
        connection.execute("INSERT INTO source VALUES(1)")
        connection.execute("INSERT INTO source VALUES(2)")
    fence = proof.response_fence(connection, capture)
    connection.execute("UPDATE navigation_meta SET epoch='new',revision=0")
    assert certify(connection, fence) == "included"
    forged = {**fence, "witnesses": []}
    assert certify(connection, forged) == "unknown"
    connection.execute(
        "DELETE FROM navigation_commit_witnesses WHERE identity=?", (capture.witnesses[0],)
    )
    connection.execute("UPDATE navigation_meta SET revision=100000")
    assert certify(connection, fence) == "unknown"


def test_expiry_before_response_stays_unknown(connection: ChangeSignallingConnection) -> None:
    with proof.capture_command_commits() as capture:
        connection.execute("INSERT INTO source VALUES(1)")
    connection.execute("INSERT INTO source VALUES(2)")
    with connection:
        proof.prune(connection, retain_count=1)
    assert certify(connection, proof.response_fence(connection, capture)) == "unknown"
    assert connection.execute("SELECT count(*) FROM navigation_commit_witnesses").fetchone()[0] == 1


def test_unrequested_writes_also_have_independent_witnesses(
    connection: ChangeSignallingConnection,
) -> None:
    connection.execute("INSERT INTO source VALUES(1)")
    assert connection.execute("SELECT count(*) FROM navigation_commit_witnesses").fetchone()[0] == 1


def test_shielded_command_child_and_thread_capture_excludes_background(
    connection: ChangeSignallingConnection, tmp_path: Path
) -> None:
    path = tmp_path / "fixture.db"

    def write(value: int) -> None:
        db = connect(str(path))
        assert isinstance(db, ChangeSignallingConnection)
        install(db)
        try:
            db.execute("INSERT INTO source VALUES(?)", (value,))
        finally:
            db.close()

    async def exercise() -> proof.Capture:
        capture = proof.begin_capture(str(path))
        task = asyncio.create_task(run_in_capture_thread(write, 1))
        register_capture_task(task)
        await asyncio.shield(task)

        async def background() -> None:
            await asyncio.to_thread(write, 2)
            await run_in_capture_thread(write, 3)

        await asyncio.create_task(background())
        await run_in_capture_thread(write, 4)
        proof.end_capture(capture)
        return capture

    capture = asyncio.run(exercise())
    assert len(capture.witnesses) == 2
    assert connection.execute("SELECT count(*) FROM source").fetchone()[0] == 4
    assert certify(connection, proof.response_fence(connection, capture)) == "included"


def test_wrong_database_excluded(connection: ChangeSignallingConnection, tmp_path: Path) -> None:
    other = fixture_database(tmp_path / "other.db")
    try:
        with proof.capture_command_commits(str(tmp_path / "fixture.db")) as capture:
            other.execute("INSERT INTO source VALUES(1)")
            connection.execute("INSERT INTO source VALUES(1)")
        assert len(capture.witnesses) == 1
        assert certify(other, proof.response_fence(other, capture)) == "unknown"
    finally:
        other.close()
