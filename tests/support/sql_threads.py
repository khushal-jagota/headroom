"""Check real SQLite execution rather than the presence of an async wrapper."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

import pytest

from planner.core.db import ChangeSignallingConnection, ParticipatingCursor


def _checked(operation: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(operation)
    def invoke(*args: Any, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return operation(*args, **kwargs)
        raise AssertionError("SQLite ran on the event loop thread")

    return invoke


@contextmanager
def sql_must_run_off_event_loop() -> Iterator[None]:
    """Reject SQL, result stepping, and connection cleanup on the event loop."""
    with pytest.MonkeyPatch.context() as patch:
        for name in ("_sqlite_execute", "_sqlite_commit", "_rollback_failed", "rollback", "close"):
            patch.setattr(
                ChangeSignallingConnection,
                name,
                _checked(getattr(ChangeSignallingConnection, name)),
            )
        for name in ("executemany", "fetchone", "fetchmany", "fetchall", "__next__", "close"):
            patch.setattr(ParticipatingCursor, name, _checked(getattr(ParticipatingCursor, name)))
        yield
