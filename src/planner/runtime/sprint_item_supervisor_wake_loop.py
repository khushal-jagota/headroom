"""Wake a Sprint Item's supervisor when something changed that it can act on.

The change signal is this loop's only trigger. It has no periodic timer, and that is
the point: a timer would answer from standing state, and a state-driven loop sends the
same message for the same standing fact for as long as the fact stands. If nothing
changes, nothing is sent, however long a Ticket sits waiting.

There is no claim, no row, no delivery id, no attempt count and no retry. A wake that
is refused is lost, and the next change sends another.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sqlite3
import threading
from collections.abc import Callable
from time import monotonic as _monotonic

from planner.conversation.contracts import ConversationSystem, PromptDeliveryRefused
from planner.conversation.message_content import text_message_content
from planner.core.clock import Clock
from planner.core.db import connect
from planner.runtime import conversation_start, sprint_item_supervisor_wake
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service

_log = logging.getLogger(__name__)


async def send_supervisor_wake(
    sprint_item_id: str,
    *,
    connect_database: Callable[[], sqlite3.Connection],
    conversation_system: ConversationSystem,
) -> bool:
    """Wake one Sprint Item's supervisor. True when a wake went to the conversation.

    The Item's lifecycle lock is held throughout, because the Item can be deleted and
    its supervisor conversation killed underneath this.
    """
    async with sprints_service.supervisor_lifecycle_lock(sprint_item_id):
        conn = connect_database()
        try:
            item = sprints_data.read_item(conn, sprint_item_id).item
            current = conversation_start.read_agent_conversation(
                conn, item.supervisor_agent_key
            )
            if current is not None:
                # A supervisor that is working, or that already has something waiting for
                # it, will read current state anyway when it gets there. A second wake
                # behind the first would only queue a hint it is about to outrun. Nothing
                # is lost: no wake is recorded, so the move still counts on the next change.
                if await conversation_system.is_running(current):
                    return False
                if await conversation_system.held_prompts(current):
                    return False
            # Asked again under the lock, where the answer is final. The message sent is
            # the one this answer carries, so it names what was true here and nowhere else.
            message = sprint_item_supervisor_wake.sprint_item_wake_message(
                conn, sprint_item_id, conversation_id=current
            )
            if message is None:
                return False
            delivered = await conversation_start.send_to_agent_conversation(
                conversation_system,
                conn,
                item.supervisor_agent_key,
                text_message_content(message),
                conversation_start.sprint_item_supervisor_resolve(item),
                conversation_id=current,
                created_conversation_id=current or conversation_start.new_conversation_id(),
                sender_label=sprint_item_supervisor_wake.WAKE_SENDER_LABEL,
                required_sprint_item_id=item.id,
            )
        finally:
            conn.close()
    if isinstance(delivered.fate, PromptDeliveryRefused):
        # Nothing is written down and nothing is tried again. The next change sends
        # another wake, and that one says exactly as much as this one would have.
        _log.info(
            "supervisor wake refused (item=%s reason=%s)",
            sprint_item_id,
            delivered.fate.refusal_reason.value,
        )
        return False
    return True


class SprintItemSupervisorWakeLoop:
    """Wake the supervisors of the Sprint Items that need one, on change."""

    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        conversation_system: ConversationSystem,
        asyncio_loop: asyncio.AbstractEventLoop,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._conversation_system = conversation_system
        self._asyncio_loop = asyncio_loop
        self._busy_timeout_ms = busy_timeout_ms
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._in_flight_lock = threading.Lock()
        self._in_flight: dict[concurrent.futures.Future[bool], str] = {}

    def wake(self) -> None:
        """Ask the loop to look now. This is the only thing that ever asks it."""
        self._wake.set()

    def _item_ids_needing_supervisor(self) -> list[str]:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            needing: list[str] = []
            for item_id in sprint_item_supervisor_wake.sprint_item_ids_to_consider(conn):
                conversation_id = conversation_start.read_agent_conversation(
                    conn, sprints_data.supervisor_agent_key(item_id)
                )
                if (
                    sprint_item_supervisor_wake.sprint_item_wake_message(
                        conn, item_id, conversation_id=conversation_id
                    )
                    is not None
                ):
                    needing.append(item_id)
            return needing
        finally:
            conn.close()

    def poll_once(self) -> list[str]:
        """Wake every Sprint Item supervisor that needs it. Returns the ids it set going."""
        return [
            item_id
            for item_id in self._item_ids_needing_supervisor()
            if self._schedule(item_id)
        ]

    def _schedule(self, sprint_item_id: str) -> bool:
        with self._in_flight_lock:
            if self._stop.is_set():
                return False
            if sprint_item_id in self._in_flight.values():
                return False
        future = asyncio.run_coroutine_threadsafe(
            send_supervisor_wake(
                sprint_item_id,
                connect_database=lambda: connect(self._db_path, self._busy_timeout_ms),
                conversation_system=self._conversation_system,
            ),
            self._asyncio_loop,
        )
        with self._in_flight_lock:
            self._in_flight[future] = sprint_item_id
        future.add_done_callback(self._wake_ended)
        return True

    def _wake_ended(self, future: concurrent.futures.Future[bool]) -> None:
        """Forget a finished wake, and say so when it ended in a way nothing else saw."""
        with self._in_flight_lock:
            sprint_item_id = self._in_flight.pop(future, None)
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            _log.error(
                "supervisor wake ended in an unhandled failure (item=%s)",
                sprint_item_id,
                exc_info=error,
            )

    def start(self) -> None:
        """Start the thread. It takes no interval, because it waits only on the signal."""
        if self._thread is not None:
            raise RuntimeError("sprint item supervisor wake loop already started")
        self._thread = threading.Thread(
            target=self._run_loop,
            name="sprint-item-supervisor-wake-loop",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, deadline: float | None = None) -> None:
        """Stop looking, wait out the wakes in flight, and cancel whatever outlasts that."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=_remaining(deadline))
            self._thread = None
        with self._in_flight_lock:
            in_flight = tuple(self._in_flight)
        if in_flight:
            _, still_going = concurrent.futures.wait(in_flight, timeout=_remaining(deadline))
            for future in still_going:
                future.cancel()

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            # Wait first. Starting is not a change, and the state that stands at startup
            # was already there when whatever last woke this supervisor was sent.
            self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.poll_once()
            except Exception:
                _log.exception("sprint item supervisor wake pass failed")


def _remaining(deadline: float | None) -> float:
    return 10.0 if deadline is None else max(0.0, deadline - _monotonic())
