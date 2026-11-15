"""What a kept read connection must still be: read-only, one instant, and let go of.

The saving is easy to get and easy to lose. These cover the three ways reuse could make a
reader worse than the per-request connection it replaces — writing, seeing a torn write,
and holding a read transaction open so the WAL can never be checkpointed — plus the one
operational escape hatch, resetting after the file underneath moves.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from planner.core.read_connections import ReadConnections


def database(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE note(id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO note VALUES (1, 'first')")
    conn.commit()
    conn.close()
    return path


def test_one_connection_serves_every_read_on_a_thread(tmp_path: Path) -> None:
    reads = ReadConnections(database(tmp_path / "kept.db"), readers=1)
    try:
        seen = [
            asyncio.run(reads.run(lambda conn: id(conn))),
            asyncio.run(reads.run(lambda conn: id(conn))),
            asyncio.run(reads.run(lambda conn: id(conn))),
        ]
        assert len(set(seen)) == 1
        assert reads.connections_open == 1
    finally:
        reads.close()


def test_a_read_cannot_write_even_by_mistake(tmp_path: Path) -> None:
    reads = ReadConnections(database(tmp_path / "ro.db"), readers=1)
    try:
        with pytest.raises(sqlite3.OperationalError):
            asyncio.run(reads.run(lambda conn: conn.execute("INSERT INTO note VALUES (2, 'no')")))
        # The refusal must not cost the connection: the next read still works, on the
        # same connection, with its transaction in a usable state.
        assert (
            asyncio.run(reads.run(lambda conn: conn.execute("SELECT body FROM note").fetchone()[0]))
            == "first"
        )
    finally:
        reads.close()


def test_each_read_ends_its_transaction_so_the_wal_can_be_checkpointed(tmp_path: Path) -> None:
    path = database(tmp_path / "snapshot.db")
    reads = ReadConnections(path, readers=1)
    try:
        asyncio.run(reads.run(lambda conn: conn.execute("SELECT 1").fetchone()))
        # A read transaction left open on a kept connection would hold the WAL at the
        # snapshot it read. Checkpointing is the observable proof that it did not.
        writer = sqlite3.connect(path)
        writer.execute("INSERT INTO note VALUES (2, 'second')")
        writer.commit()
        busy, log, checkpointed = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        writer.close()
        assert busy == 0, "a kept reader was still holding the WAL"

        # And the next read sees the write that followed it, rather than its old instant.
        assert (
            asyncio.run(
                reads.run(lambda conn: conn.execute("SELECT COUNT(*) FROM note").fetchone()[0])
            )
            == 2
        )
    finally:
        reads.close()


def test_reset_drops_kept_connections_for_a_file_that_moved(tmp_path: Path) -> None:
    path = database(tmp_path / "moved.db")
    reads = ReadConnections(path, readers=1)
    try:
        assert (
            asyncio.run(
                reads.run(lambda conn: conn.execute("SELECT COUNT(*) FROM note").fetchone()[0])
            )
            == 1
        )
        replacement = database(tmp_path / "replacement.db")
        writer = sqlite3.connect(replacement)
        writer.execute("INSERT INTO note VALUES (2, 'second')")
        writer.commit()
        writer.close()
        replacement.replace(path)

        reads.reset()
        assert reads.connections_open == 0
        assert (
            asyncio.run(
                reads.run(lambda conn: conn.execute("SELECT COUNT(*) FROM note").fetchone()[0])
            )
            == 2
        )
    finally:
        reads.close()


def test_a_closed_pool_refuses_new_reads(tmp_path: Path) -> None:
    reads = ReadConnections(database(tmp_path / "closed.db"), readers=1)
    reads.close()
    with pytest.raises(RuntimeError):
        reads.run_here(lambda conn: conn.execute("SELECT 1").fetchone())
