"""Database module composition shared by every core connection.

Modules attach regardless of the schema version at connection creation. Their
participants must tolerate a database before their migration exists.
"""

from __future__ import annotations

import sqlite3

from planner.core.transaction_participants import TransactionParticipant


def attach(connection: sqlite3.Connection) -> tuple[TransactionParticipant, ...]:
    """Attach database modules and return their synchronous commit participants."""
    from planner import navigation

    navigation.attach(connection)
    return (navigation.finalize,)
