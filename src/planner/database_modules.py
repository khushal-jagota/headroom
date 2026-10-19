"""Database module composition shared by every core connection.

Modules attach regardless of the schema version at connection creation. Their
participants must tolerate a database before their migration exists.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from planner.core.commit_observers import CommitObserver
from planner.core.transaction_participants import TransactionParticipant


class SignalModule(Protocol):
    def attach_connection(self, connection: sqlite3.Connection) -> None: ...
    def finalize_signal(self, connection: sqlite3.Connection, /) -> None: ...


_signal_lock = threading.Lock()
_signal_modules: dict[str, SignalModule] = {}


def _identity(connection: sqlite3.Connection) -> str | None:
    for row in connection.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return str(Path(row[2]).resolve())
    return None


class SignalRegistration:
    def __init__(self, identity: str, source: SignalModule) -> None:
        self.identity = identity
        self.source = source
        self.closed = False

    def close(self) -> None:
        with _signal_lock:
            if self.closed:
                return
            if _signal_modules.get(self.identity) is not self.source:
                raise RuntimeError("Signal module registration changed")
            del _signal_modules[self.identity]
            self.closed = True


def register_signal_publication(database_path: str, source: SignalModule) -> SignalRegistration:
    identity = str(Path(database_path).resolve())
    with _signal_lock:
        if identity in _signal_modules:
            raise RuntimeError("Signal database module is already registered")
        _signal_modules[identity] = source
    return SignalRegistration(identity, source)


def _source(connection: sqlite3.Connection) -> SignalModule | None:
    identity = _identity(connection)
    with _signal_lock:
        return _signal_modules.get(identity) if identity is not None else None


def _finalize_signal(connection: sqlite3.Connection, /) -> None:
    source = _source(connection)
    if source is not None:
        source.finalize_signal(connection)


def attach(connection: sqlite3.Connection) -> tuple[TransactionParticipant, ...]:
    """Attach database modules and return their synchronous commit participants."""
    from planner import navigation
    from planner.files.catalog.links import finalize as finalize_artifact_links
    from planner.navigation import proof

    navigation.attach(connection)
    source = _source(connection)
    if source is not None:
        source.attach_connection(connection)
    return (_finalize_signal, finalize_artifact_links, proof.prepare_witness, navigation.finalize)


def commit_observers() -> tuple[CommitObserver, ...]:
    """Compose domain observers without domain branches in core."""
    from planner.navigation.proof import COMMIT_OBSERVER

    return (COMMIT_OBSERVER,)
