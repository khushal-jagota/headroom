"""Transfers reach the old holder through the real conversation system."""

from __future__ import annotations

from sqlite3 import Connection

from tests.unit.test_conversation_system import _Harness


def _system(conn: Connection) -> _Harness:
    return _Harness(conn.execute("PRAGMA database_list").fetchone()[2])
