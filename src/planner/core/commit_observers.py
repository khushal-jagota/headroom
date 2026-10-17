"""Generic successful-commit observers. Observers cannot change commit outcomes."""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CommitObserver:
    committed: Callable[[sqlite3.Connection, tuple[object, ...]], object]
    failed: Callable[[tuple[object, ...]], None]


def observe(
    connection: sqlite3.Connection,
    results: tuple[object, ...],
    observers: tuple[CommitObserver, ...],
) -> None:
    for observer in observers:
        try:
            result = observer.committed(connection, results)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("commit observers must be synchronous")
        except BaseException:
            # A durable command must retain its original successful receipt even if
            # evidence collection fails. Failure callbacks are equally isolated.
            try:
                observer.failed(results)
            except BaseException:
                pass
