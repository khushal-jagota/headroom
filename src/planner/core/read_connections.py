"""Kept read-only connections, so a read does not pay for a connection to make one.

Opening a SQLite connection is not free on this database. The first statement on a fresh
connection measures 2.39 ms against 0.022 ms for the second, because the connection has
to load the schema before it can plan anything, and this schema is 80 tables and 313
``sqlite_master`` entries. Every HTTP read in the conversation view paid that twice: once
for its own read-only connection and once more for a second connection opened inside it.

So reads run here instead. One connection per worker thread, opened on first use and kept
for the life of the process, with its own small executor so the number of connections is a
decided number rather than whatever the default thread pool happens to grow to.

Two properties this keeps exactly as they were, because a reader that loses them is worse
than a slow one:

* **It cannot write.** The connection is opened ``mode=ro`` and told ``query_only=ON``,
  which is per-connection and sticky, so a read path cannot acquire the habit.
* **It sees one instant.** Each operation runs inside its own ``BEGIN``, which gives the
  same stable snapshot a per-request connection gave. The difference that matters: that
  transaction is now *ended* explicitly, because a kept connection sitting in a read
  transaction would pin the WAL and stop it being checkpointed.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

DEFAULT_READERS = 6
"""Threads, and therefore connections. Reads here run in single-figure milliseconds, and
every live conversation tail performs one on each wake, so a small bounded queue is the
behaviour wanted: it is what keeps a burst of tails from opening a connection each."""


class ReadConnections:
    """Read-only access to one database, over connections that are kept.

    An operation receives a connection with ``sqlite3.Row`` rows and an open read
    transaction, and must not retain it: the connection belongs to the thread, not to the
    caller, and the next operation on that thread will start a new transaction on it.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        readers: int = DEFAULT_READERS,
        name: str = "read",
    ) -> None:
        if readers < 1:
            raise ValueError("a reader pool needs at least one reader")
        self._uri = Path(database_path).resolve().as_uri() + "?mode=ro"
        self._local = threading.local()
        self._guard = threading.Lock()
        self._open: list[sqlite3.Connection] = []
        self._closed = False
        # Imported here so that the module stays importable without the executor being
        # built, which matters for the process-wide import graph more than it reads.
        from concurrent.futures import ThreadPoolExecutor

        self._executor = ThreadPoolExecutor(max_workers=readers, thread_name_prefix=name)

    async def run[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Run one read on a kept connection, inside its own read transaction."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._run_here, operation)

    def run_here[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Run one read on the calling thread's connection.

        For a caller already off the event loop. It opens a connection for that thread
        like any other, so a thread that borrows this once holds a connection afterwards.
        """
        return self._run_here(operation)

    def _run_here[T](self, operation: Callable[[sqlite3.Connection], T]) -> T:
        conn = self._connection()
        conn.execute("BEGIN")
        try:
            result = operation(conn)
        except BaseException:
            # The snapshot has to be released whatever happened, or this connection keeps
            # the WAL from being checkpointed for as long as the process lives.
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return result

    def _connection(self) -> sqlite3.Connection:
        existing: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if existing is not None:
            return existing
        with self._guard:
            if self._closed:
                raise RuntimeError("read connections are closed")
        conn = sqlite3.connect(
            self._uri,
            uri=True,
            isolation_level=None,
            # The connection is only ever used by its own thread. This is set so that
            # closing it from shutdown, which happens on another thread, is legal.
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        with self._guard:
            if self._closed:
                conn.close()
                raise RuntimeError("read connections are closed")
            self._open.append(conn)
        self._local.conn = conn
        return conn

    def reset(self) -> None:
        """Drop every kept connection; the next read opens a fresh one.

        For the case where the file underneath has been replaced rather than written to.
        A kept connection would go on reading an unlinked inode, where a per-request
        connection would have picked up the new file by accident. Restoring a backup stops
        this server first, so nothing calls this today; it exists so that the answer to
        "what if the file moves" is a method and not a restart.

        Call it with no read in flight. It closes connections other threads may be holding,
        which is safe when the server is quiesced and is not safe otherwise.
        """
        with self._guard:
            connections, self._open = self._open, []
        self._local = threading.local()
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        """Stop accepting reads, then close every connection."""
        with self._guard:
            self._closed = True
        self._executor.shutdown(wait=True)
        self.reset()

    @property
    def connections_open(self) -> int:
        """How many connections are being kept. For tests and for saying so out loud."""
        with self._guard:
            return len(self._open)
