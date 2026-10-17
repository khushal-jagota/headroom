"""Database module composition shared by every core connection.

Modules attach regardless of the schema version at connection creation. Their
participants must tolerate a database before their migration exists.
"""

from __future__ import annotations

import sqlite3

from planner.core.commit_observers import CommitObserver
from planner.core.transaction_participants import TransactionParticipant


def attach(connection: sqlite3.Connection) -> tuple[TransactionParticipant, ...]:
    """Attach database modules and return their synchronous commit participants."""
    from planner import navigation
    from planner.navigation import proof

    navigation.attach(connection)
    return (proof.prepare_witness, navigation.finalize)


def commit_observers() -> tuple[CommitObserver, ...]:
    """Compose domain observers without domain branches in core."""
    from planner.navigation.proof import COMMIT_OBSERVER

    return (COMMIT_OBSERVER,)
