"""Every committed transaction announces itself, exactly once, and nothing else does."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from planner.core import change_signal
from planner.core.db import connect, create_schema


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
