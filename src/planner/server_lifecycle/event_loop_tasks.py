"""The asyncio tasks on the application's main event loop: `panels server-event-loop-tasks`.

A stall that waits inside an asyncio task leaves every thread idle, so a native stack
dump shows nothing. This module lets the running application describe its own tasks:
each one's name, coroutine, the await chain down to where it is waiting, and its age.

Everything here only reads. Python keeps no creation time for a task, so the loop's
`create_task` is wrapped to note one in a weak mapping; the task itself is untouched and
is forgotten with it. The answer is built in one synchronous pass on the loop thread,
so no task advances while it is being described, and it is never cancelled or woken.
"""

from __future__ import annotations

import asyncio
import logging
import time
import types
import weakref
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from planner.server_lifecycle.configuration import (
    EVENT_LOOP_TASKS_EXCHANGE_SECONDS,
    MAX_EVENT_LOOP_TASKS_RESPONSE_BYTES,
)
from planner.server_lifecycle.contracts import (
    EventLoopTask,
    EventLoopTaskFrame,
    EventLoopTasksError,
)
from planner.server_lifecycle.control import (
    EVENT_LOOP_TASKS_RESPONSE_ENVELOPE_BYTES,
    MAX_CONTROL_REQUEST_BYTES,
    decode_event_loop_tasks_request,
    encode_event_loop_task,
    encode_event_loop_tasks_response,
)

_log = logging.getLogger(__name__)
_AWAITED_ATTRIBUTES = ("cr_await", "gi_yieldfrom", "ag_await")
_FRAME_ATTRIBUTES = ("cr_frame", "gi_frame", "ag_frame")


class TaskCreationTimes:
    """Note when each task on one loop is created, without holding the task alive."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._created_at: weakref.WeakKeyDictionary[asyncio.Task[Any], float] = (
            weakref.WeakKeyDictionary()
        )
        self._recording_create_task: Callable[..., asyncio.Task[Any]] | None = None

    def install(self) -> None:
        # A task factory would be the documented hook, but on Python 3.13 the loop renames
        # every factory-made task without a name to "None". Wrapping this loop's own
        # create_task keeps names, types and scheduling exactly as they were.
        create_task = self._loop.create_task
        created_at = self._created_at

        def create_task_noting_creation_time(*args: Any, **kwargs: Any) -> asyncio.Task[Any]:
            task = create_task(*args, **kwargs)
            created_at[task] = time.monotonic()
            return task

        # Tasks that already exist were created no later than now.
        now = time.monotonic()
        for task in asyncio.all_tasks(self._loop):
            created_at[task] = now
        self._loop.create_task = create_task_noting_creation_time  # type: ignore[method-assign]
        self._recording_create_task = create_task_noting_creation_time

    def uninstall(self) -> None:
        if self._recording_create_task is None:
            return
        if vars(self._loop).get("create_task") is self._recording_create_task:
            del self._loop.create_task
        self._recording_create_task = None

    def created_at(self, task: asyncio.Task[Any]) -> float | None:
        return self._created_at.get(task)


def describe_task(task: asyncio.Task[Any], created_at: float | None, now: float) -> EventLoopTask:
    coroutine = task.get_coro()
    return EventLoopTask(
        name=task.get_name(),
        coroutine=getattr(coroutine, "__qualname__", type(coroutine).__qualname__),
        age_seconds=None if created_at is None else round(now - created_at, 3),
        stack=_await_chain(coroutine),
    )


def _await_chain(coroutine: object) -> tuple[EventLoopTaskFrame, ...]:
    """Follow what each coroutine awaits, outermost first, to the innermost frame."""
    frames: list[EventLoopTaskFrame] = []
    current: object | None = coroutine
    while current is not None:
        frame = _first_attribute(current, _FRAME_ATTRIBUTES)
        if not isinstance(frame, types.FrameType):
            break
        frames.append(
            EventLoopTaskFrame(
                file=frame.f_code.co_filename,
                line=frame.f_lineno,
                function=frame.f_code.co_qualname,
            )
        )
        current = _first_attribute(current, _AWAITED_ATTRIBUTES)
    return tuple(frames)


def _first_attribute(value: object, names: tuple[str, ...]) -> object | None:
    for name in names:
        found: object | None = getattr(value, name, None)
        if found is not None:
            return found
    return None


def build_event_loop_tasks_answer(
    loop: asyncio.AbstractEventLoop,
    creation_times: TaskCreationTimes,
    *,
    max_bytes: int = MAX_EVENT_LOOP_TASKS_RESPONSE_BYTES,
) -> bytes:
    """Describe every live task, oldest first, until the answer would pass `max_bytes`.

    Tasks whose creation was not noted come last. The rest are counted, never described,
    so a loop with very many tasks costs one sort and a bounded number of stack walks.
    """
    now = time.monotonic()
    tasks = [(creation_times.created_at(task), task) for task in asyncio.all_tasks(loop)]
    tasks.sort(key=lambda pair: (pair[0] is None, pair[0] or 0.0))
    budget = max_bytes - EVENT_LOOP_TASKS_RESPONSE_ENVELOPE_BYTES
    encoded: list[bytes] = []
    for created_at, task in tasks:
        entry = encode_event_loop_task(describe_task(task, created_at, now))
        # Every entry after the first is preceded by a comma.
        cost = len(entry) + (1 if encoded else 0)
        if cost > budget:
            break
        budget -= cost
        encoded.append(entry)
    return encode_event_loop_tasks_response(
        total_task_count=len(tasks),
        omitted_task_count=len(tasks) - len(encoded),
        encoded_tasks=encoded,
    )


class EventLoopTasksListing:
    """The open event-loop-tasks socket and the creation times it reports."""

    def __init__(
        self,
        tasks_socket_path: Path,
        creation_times: TaskCreationTimes,
        listener: asyncio.Server,
    ) -> None:
        self._tasks_socket_path = tasks_socket_path
        self._creation_times = creation_times
        self._listener: asyncio.Server | None = listener

    def close(self) -> None:
        """Stop answering, remove the socket, and stop noting creation times. Idempotent."""
        if self._listener is None:
            return
        self._listener.close()
        self._listener = None
        self._tasks_socket_path.unlink(missing_ok=True)
        self._creation_times.uninstall()


async def open_event_loop_tasks_listing(tasks_socket_path: Path) -> EventLoopTasksListing | None:
    """Start noting creation times and answering on `tasks_socket_path`, on this loop.

    The listing is a diagnostic beside the server. If its socket cannot be bound, nothing
    is noted, the server still runs, and the reason is logged.
    """
    loop = asyncio.get_running_loop()
    creation_times = TaskCreationTimes(loop)
    creation_times.install()
    try:
        listener = await asyncio.start_unix_server(
            partial(
                _answer_one_client, lambda: build_event_loop_tasks_answer(loop, creation_times)
            ),
            path=str(tasks_socket_path),
            # A request line keeps the bound of every control request.
            limit=MAX_CONTROL_REQUEST_BYTES,
        )
    except OSError as exc:
        creation_times.uninstall()
        _log.warning("Could not serve the event loop tasks at %s: %s", tasks_socket_path, exc)
        return None
    except BaseException:
        creation_times.uninstall()
        raise
    return EventLoopTasksListing(tasks_socket_path, creation_times, listener)


async def _answer_one_client(
    build_answer: Callable[[], bytes],
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Answer one well-formed request; drop a malformed, oversized, or stalled exchange."""
    try:
        async with asyncio.timeout(EVENT_LOOP_TASKS_EXCHANGE_SECONDS):
            payload = await reader.readuntil(b"\n")
            decode_event_loop_tasks_request(payload)
            writer.write(build_answer())
            await writer.drain()
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, EventLoopTasksError):
        pass
    except (TimeoutError, OSError):
        # A stalled reader must not keep a buffered answer alive.
        writer.transport.abort()
    finally:
        writer.close()
