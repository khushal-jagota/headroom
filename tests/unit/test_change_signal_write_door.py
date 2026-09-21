"""Every committed transaction announces itself, exactly once, and nothing else does."""

from __future__ import annotations

import sqlite3
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


def test_a_transaction_ended_with_the_commit_method_signals_once(
    conn: sqlite3.Connection, signals: _SignalCounter
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO door (note) VALUES ('one')")
    conn.commit()

    assert signals.count == 1


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
