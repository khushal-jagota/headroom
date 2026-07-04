"""Shared fixtures for the unit suite. Owned by the orchestrator (glue);
ticket agents use these read-only and add their own fixtures locally.
"""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock
from planner.core.config import Config, load_config
from planner.core.db import connect, create_schema


@pytest.fixture
def cfg() -> Config:
    """Default configuration, isolated from process env."""
    return load_config(path=None, env={})


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Connection]:
    """A fresh schema on a temp SQLite file."""
    conn = connect(str(tmp_path / "planning-test.db"))
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def fake_clock() -> TestClock:
    """Mutable clock pinned to a mid-day baseline; tests move it as needed."""
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
