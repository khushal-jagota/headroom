"""Current navigation fixtures own a real producer lifetime over synthetic storage."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from planner.conversation.contracts import SignalAttachment
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.core.db import ChangeSignallingConnection
from planner.navigation.signal_publication import install, participate


@contextmanager
def current_navigation_database(path: Path) -> Iterator[sqlite3.Connection]:
    """Install the canonical producer over an explicitly empty synthetic runtime.

    No provider or conversation turn exists in this fixture. The domain canonical
    reader, rather than a supplied attention payload, computes all publications.
    The source lease covers every connection until this context closes.
    """
    composition = SignalRuntimeComposition(str(path), 1000)
    attachment: SignalAttachment | None = None
    connection: ChangeSignallingConnection | None = None
    try:
        composition.bootstrap()
        composition.coordinator.bind_runtime(lambda identity: None)
        composition.initialize()
        attachment = composition.coordinator.attach_signal_publication(
            install=install,
            participate=participate,
            unavailable=lambda invalidation: None,
        )
        composition.coordinator.admit_runtime()
        connection = composition.connect()
        yield connection
    finally:
        if connection is not None:
            connection.close()
        if attachment is not None:
            attachment.close()
        composition.close()


@contextmanager
def prepared_navigation_database(path: Path) -> Iterator[sqlite3.Connection]:
    """Inspect physical schema preparation under a new canonical bootstrap lease.

    Current publication installation is deliberately absent. This context proves
    physical second-open behavior before runtime-dependent continuity changes.
    """
    composition = SignalRuntimeComposition(str(path), 1000)
    connection: ChangeSignallingConnection | None = None
    try:
        composition.bootstrap()
        connection = composition.connect()
        yield connection
    finally:
        if connection is not None:
            connection.close()
        composition.close()
