"""Bounded connections for audited, synchronous runtime database operations.

Unlike public ``db.connect``, this is an explicitly scoped loan: neither the
connection nor a cursor may escape the operation. Borrowers use ordinary queries
and managed transactions; connection-local functions, PRAGMAs, temporary objects,
attached databases and extensions belong on a fresh connection instead.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from planner.core.config import DB_BUSY_TIMEOUT_MS, RUNTIME_DATABASE_CONNECTIONS
from planner.core.db import ChangeSignallingConnection, connect


class RuntimeConnections:
    def __init__(self, path: str, *, capacity: int = RUNTIME_DATABASE_CONNECTIONS) -> None:
        if capacity < 1:
            raise ValueError("a connection pool needs positive capacity")
        self.path = path
        self.capacity = capacity
        self._condition = threading.Condition()
        self._idle: list[ChangeSignallingConnection] = []
        self._open = 0
        self._owners: set[int] = set()
        self._closed = False

    @property
    def connections_open(self) -> int:
        with self._condition:
            return self._open

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        owner = threading.get_ident()
        with self._condition:
            if owner in self._owners:
                raise RuntimeError("nested runtime connection loan; pass the existing connection")
            while not self._closed and not self._idle and self._open >= self.capacity:
                self._condition.wait()
            if self._closed:
                raise RuntimeError("runtime connections are closed")
            self._owners.add(owner)
            conn = self._idle.pop() if self._idle else None
            if conn is None:
                self._open += 1
        try:
            if conn is None:
                conn = cast(ChangeSignallingConnection, connect(self.path))
        except BaseException:
            with self._condition:
                self._open -= 1
                self._owners.remove(owner)
                self._condition.notify_all()
            raise
        reusable = False
        try:
            yield conn
            reusable = True
        finally:
            # A failed operation is discarded, as is a borrower that left a guard,
            # authorizer or context installed. Never revive a physically closed handle.
            reusable = reusable and not (
                conn._closed or conn._finalizing or conn._control_guard_depth
                or conn._contexts or conn._authorizer is not None
            )
            try:
                if not conn._closed:
                    conn.rollback()
                    conn.row_factory = sqlite3.Row
                    conn.text_factory = str
            except BaseException:
                reusable = False
                raise
            finally:
                with self._condition:
                    self._owners.remove(owner)
                    if reusable and not self._closed:
                        self._idle.append(conn)
                    else:
                        # Quarantine first: close can itself refuse a dirty guard.
                        self._open -= 1
                        sqlite3.Connection.close(conn)
                        conn._closed = True
                    self._condition.notify_all()

    def close(self) -> None:
        """Retire idle handles now; outstanding loans retire when returned."""
        with self._condition:
            self._closed = True
            for conn in self._idle:
                conn.close()
            self._open -= len(self._idle)
            self._idle.clear()
            self._condition.notify_all()


_registry_lock = threading.Lock()
_registry: dict[str, RuntimeConnections] = {}


def _key(path: str) -> str | None:
    if not path or path == ":memory:" or path.startswith("file:"):
        return None
    return str(Path(path).resolve())


@contextmanager
def reuse_connections(path: str) -> Iterator[RuntimeConnections]:
    """Own one home's pool for its server lifespan."""
    key = _key(path)
    if key is None:
        raise ValueError("runtime reuse needs a regular database file")
    pool = RuntimeConnections(key)
    with _registry_lock:
        if key in _registry:
            raise RuntimeError("runtime connections are already registered")
        _registry[key] = pool
    try:
        yield pool
    finally:
        with _registry_lock:
            del _registry[key]
        pool.close()


@contextmanager
def runtime_connection(
    path: str, busy_timeout_ms: int = DB_BUSY_TIMEOUT_MS,
) -> Iterator[sqlite3.Connection]:
    """Loan a runtime connection, or own a fresh connection outside a runtime."""
    key = _key(path)
    with _registry_lock:
        pool = _registry.get(key) if key is not None else None
    if pool is not None and busy_timeout_ms == DB_BUSY_TIMEOUT_MS:
        with pool.connection() as conn:
            yield conn
    else:
        conn = connect(path, busy_timeout_ms)
        try:
            yield conn
        finally:
            conn.close()
