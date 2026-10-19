"""Synchronous database participants, called before the outer transaction commits."""

from __future__ import annotations

import inspect
import sqlite3
from typing import Protocol


class TransactionParticipant(Protocol):
    def __call__(self, connection: sqlite3.Connection, /) -> object: ...


def finalize(
    connection: sqlite3.Connection, participants: tuple[TransactionParticipant, ...]
) -> tuple[object, ...]:
    """Run participants on the source connection without asynchronous work."""
    results: list[object] = []
    for participant in participants:
        result = participant(connection)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("transaction participants must be synchronous")
        results.append(result)
    return tuple(results)
