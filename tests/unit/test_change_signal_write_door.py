"""Every committed transaction announces itself, exactly once, and nothing else does."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from planner.core import change_signal
from planner.core.db import commit_without_change_signal, connect, create_schema


class _SignalCounter:
    def __init__(self) -> None:
        self.count = 0

    def record(self) -> None:
        self.count += 1

    def reset(self) -> None:
        self.count = 0


@pytest.fixture
def signals() -> Iterator[_SignalCounter]:
    counter = _SignalCounter()
    unsubscribe = change_signal.subscribe(counter.record)
    try:
        yield counter
    finally:
        unsubscribe()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(str(tmp_path / "door.db"))
    create_schema(connection)
    connection.execute("CREATE TABLE IF NOT EXISTS door (id INTEGER PRIMARY KEY, note TEXT)")
    try:
        yield connection
    finally:
        connection.close()


def test_a_transaction_ended_with_a_commit_statement_signals_once(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('one')")
    conn.execute("INSERT INTO door (note) VALUES ('two')")
    assert signals.count == 0
    conn.execute("COMMIT")

    assert signals.count == 1
    assert conn.execute("SELECT COUNT(*) FROM door").fetchone()[0] == 2


def test_a_transaction_ended_with_the_commit_method_signals_once(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('one')")
    conn.commit()

    assert signals.count == 1


def test_the_commit_method_on_a_connection_with_nothing_open_signals_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.commit()

    assert signals.count == 0


def test_a_bare_write_with_no_transaction_open_signals_once(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    """These connections leave transactions to the caller, so this write is already in."""
    conn.execute("INSERT INTO door (note) VALUES ('straight in')")

    assert signals.count == 1
    assert conn.execute("SELECT COUNT(*) FROM door").fetchone()[0] == 1

    conn.execute("UPDATE door SET note = 'edited' WHERE note = 'straight in'")
    assert signals.count == 2

    conn.execute("DELETE FROM door WHERE note = 'edited'")
    assert signals.count == 3


def test_a_bare_read_signals_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("INSERT INTO door (note) VALUES ('a row to read')")
    signals.reset()

    conn.execute("SELECT * FROM door").fetchall()
    conn.execute("SELECT COUNT(*) FROM door").fetchone()
    conn.execute("PRAGMA busy_timeout").fetchone()

    assert signals.count == 0


def test_a_bare_write_that_changes_no_rows_signals_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("UPDATE door SET note = 'nobody' WHERE note = 'no such row'")
    conn.execute("DELETE FROM door WHERE note = 'no such row'")

    assert signals.count == 0


def test_a_schema_change_signals_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("CREATE TABLE later (id INTEGER PRIMARY KEY)")
    conn.execute("DROP TABLE later")

    assert signals.count == 0


def test_statements_inside_a_transaction_wait_for_its_commit(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('one')")
    conn.execute("INSERT INTO door (note) VALUES ('two')")
    conn.execute("SELECT COUNT(*) FROM door").fetchone()
    assert signals.count == 0

    conn.execute("COMMIT")
    assert signals.count == 1


def test_a_committed_transaction_that_changed_no_rows_signals_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    """Background loops open a transaction every pass and are woken by this signal;
    an empty commit that announced would busy-spin the loop that made it."""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("SELECT COUNT(*) FROM door").fetchone()
    conn.execute("UPDATE door SET note = 'nobody' WHERE note = 'no such row'")
    conn.execute("COMMIT")

    assert signals.count == 0

    conn.execute("BEGIN IMMEDIATE")
    conn.execute("SELECT COUNT(*) FROM door").fetchone()
    conn.commit()

    assert signals.count == 0


def test_a_writing_transaction_after_an_empty_one_still_signals(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("COMMIT")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('real work')")
    conn.execute("COMMIT")

    assert signals.count == 1


def test_opening_a_transaction_signals_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("BEGIN IMMEDIATE")

    assert signals.count == 0
    conn.execute("ROLLBACK")


def test_a_rollback_signals_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('discarded')")
    conn.execute("ROLLBACK")

    assert signals.count == 0
    assert conn.execute("SELECT COUNT(*) FROM door").fetchone()[0] == 0


def test_a_commit_that_fails_signals_nothing(
    tmp_path: Path, conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    # A deferred foreign key violation is only found when the transaction tries to close,
    # so this is a commit that is asked for and refused.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE child (id INTEGER PRIMARY KEY, ticket_id TEXT NOT NULL "
        "REFERENCES tickets(id) DEFERRABLE INITIALLY DEFERRED)"
    )
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO child (ticket_id) VALUES ('t_missing')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("COMMIT")

    assert signals.count == 0
    conn.execute("ROLLBACK")
    assert signals.count == 0


def test_reads_and_uncommitted_writes_signal_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("SELECT COUNT(*) FROM door").fetchone()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('still open')")

    assert signals.count == 0
    conn.execute("ROLLBACK")


def test_a_commit_on_another_thread_reaches_the_subscriber(tmp_path: Path) -> None:
    db_path = str(tmp_path / "cross-thread.db")
    boot = connect(db_path)
    create_schema(boot)
    boot.execute("CREATE TABLE door (id INTEGER PRIMARY KEY, note TEXT)")
    boot.close()

    delivered = threading.Event()
    unsubscribe = change_signal.subscribe(delivered.set)

    def writer() -> None:
        connection = connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO door (note) VALUES ('from a thread')")
            connection.execute("COMMIT")
        finally:
            connection.close()

    try:
        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(5)
        assert delivered.wait(5)
    finally:
        unsubscribe()


def test_the_signal_arrives_after_the_write_lock_is_released(tmp_path: Path) -> None:
    """A subscriber can read the committed rows on its own connection straight away."""
    db_path = str(tmp_path / "released.db")
    writer = connect(db_path)
    create_schema(writer)
    writer.execute("CREATE TABLE door (id INTEGER PRIMARY KEY, note TEXT)")

    seen: list[int] = []

    def read_through_a_second_connection() -> None:
        reader = connect(db_path)
        try:
            seen.append(int(reader.execute("SELECT COUNT(*) FROM door").fetchone()[0]))
        finally:
            reader.close()

    unsubscribe = change_signal.subscribe(read_through_a_second_connection)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO door (note) VALUES ('visible')")
        writer.execute("COMMIT")
    finally:
        unsubscribe()
        writer.close()

    assert seen == [1]


def test_a_transaction_ended_quietly_keeps_its_work_and_announces_nothing(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    """The one way past the door, for writes no reader is waiting for."""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('nobody is waiting')")
    commit_without_change_signal(conn)

    assert signals.count == 0
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM door").fetchone()[0] == 1


def test_the_next_transaction_on_a_connection_that_committed_quietly_still_announces(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    """Going quiet is a decision about one commit, never a state the connection stays in."""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('quiet')")
    commit_without_change_signal(conn)

    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('out loud')")
    conn.execute("COMMIT")

    assert signals.count == 1
