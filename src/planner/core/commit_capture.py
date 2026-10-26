"""Command-scoped capture propagated only to explicitly awaited child tasks."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from planner.core.commit_capture_config import MAX_CAPTURE_ITEMS


def _task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@dataclass(eq=False)
class Capture:
    db_path: str | None
    max_witnesses: int
    witnesses: list[str] = field(default_factory=list)
    complete: bool = True
    closed: bool = False
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    owner_thread: int = field(default_factory=threading.get_ident)
    owner_task: asyncio.Task[Any] | None = field(default_factory=_task)
    lock: Any = field(default_factory=threading.Lock)
    token: Token[Capture | None] | None = None


_THREAD_CAPTURE: ContextVar[Capture | None] = ContextVar("command_thread_capture", default=None)


async def run_in_capture_thread[Result](operation: Callable[..., Result], *args: Any) -> Result:
    """Propagate only the explicitly awaited caller into a worker thread."""
    capture = _current_capture()
    token = _THREAD_CAPTURE.set(capture)
    try:
        return await asyncio.to_thread(operation, *args)
    except BaseException:
        if capture is not None:
            with capture.lock:
                capture.complete = False
        raise
    finally:
        _THREAD_CAPTURE.reset(token)


_CAPTURE: ContextVar[Capture | None] = ContextVar("command_capture", default=None)


def _current_capture() -> Capture | None:
    capture = _CAPTURE.get()
    if capture is None or capture.closed:
        return None
    task = _task()
    if task is None and threading.get_ident() != capture.owner_thread:
        if _THREAD_CAPTURE.get() is not capture:
            return None
    if task is not None and task is not capture.owner_task and task not in capture.tasks:
        return None
    return capture


def begin_capture(db_path: str | None = None, max_witnesses: int = MAX_CAPTURE_ITEMS) -> Capture:
    if not 1 <= max_witnesses <= MAX_CAPTURE_ITEMS:
        raise ValueError("capture witness limit is out of bounds")
    capture = Capture(str(Path(db_path).resolve()) if db_path else None, max_witnesses)
    capture.token = _CAPTURE.set(capture)
    return capture


def end_capture(capture: Capture) -> None:
    with capture.lock:
        if any(not task.done() for task in capture.tasks):
            capture.complete = False
        capture.closed = True
    if capture.token is not None:
        _CAPTURE.reset(capture.token)
        capture.token = None


@contextmanager
def capture_command_commits(
    db_path: str | None = None, max_witnesses: int = MAX_CAPTURE_ITEMS
) -> Iterator[Capture]:
    capture = begin_capture(db_path, max_witnesses)
    try:
        yield capture
    finally:
        end_capture(capture)


def register_capture_task(task: asyncio.Task[Any]) -> None:
    """Explicitly admit an awaited command child, never arbitrary background tasks."""
    capture = _current_capture()
    if capture is not None:
        capture.tasks.add(task)
        task.add_done_callback(capture.tasks.discard)
