"""Single-owner reconciliation and delivery loop for durable manager notices."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import monotonic
from uuid import uuid4

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryMode,
    PromptDeliveryRefused,
    PromptDeliveryUncertain,
)
from planner.conversation.message_content import text_message_content
from planner.core.clock import Clock
from planner.core.contracts import PrincipalKind
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.manager_wakes import data
from planner.manager_wakes.contracts import WakeBatch, WakeBatchStatus
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.tickets import data as tickets_data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY

_LOG = logging.getLogger(__name__)
WAKE_SENDER_LABEL = "Panels"


@asynccontextmanager
async def _no_lifecycle_lock() -> AsyncIterator[None]:
    yield


def reconcile_batch_outcomes(conn: sqlite3.Connection, *, now: int) -> int:
    """Settle attempts only from their exact durable conversation event."""
    settled = 0
    for batch in data.batches_waiting_for_outcome(conn):
        if batch.conversation_id is None:
            continue
        outcome = data.delivery_outcome_kind(
            conn, batch.conversation_id, batch.sender_message_id
        )
        if outcome == "prompt":
            data.close_delivered_batch(conn, batch.id, now=now)
            settled += 1
        elif outcome == "prompt_delivery_refused":
            data.record_batch_failure(
                conn,
                batch.id,
                status=WakeBatchStatus.refused,
                conversation_id=batch.conversation_id,
                now=now,
            )
            settled += 1
        elif outcome == "prompt_discarded":
            data.record_batch_failure(
                conn,
                batch.id,
                status=WakeBatchStatus.discarded,
                conversation_id=batch.conversation_id,
                now=now,
            )
            settled += 1
        elif (
            outcome == "prompt_delivery_uncertain"
            and batch.status is not WakeBatchStatus.uncertain
        ):
            data.record_batch_uncertain(
                conn,
                batch.id,
                conversation_id=batch.conversation_id,
                process_token=batch.process_token or "unknown",
                now=now,
            )
            settled += 1
        # An absent outcome and a known uncertain outcome remain open.
    return settled


async def deliver_batch(
    batch: WakeBatch,
    *,
    connect_database: Callable[[], sqlite3.Connection],
    conversation_system: ConversationSystem,
    process_token: str,
    now: Callable[[], int],
) -> bool:
    """Deliver one immutable batch through its principal's ordinary conversation door."""
    conn = connect_database()
    intended_conversation_id: str | None = None
    try:
        target = batch.target
        lifecycle = (
            sprints_service.supervisor_lifecycle_lock(target.id)
            if target.kind is PrincipalKind.sprint_item
            else _no_lifecycle_lock()
        )
        async with lifecycle:
            try:
                if target.kind is PrincipalKind.sprint_item:
                    item = sprints_data.read_item(conn, target.id).item
                    agent_key = item.supervisor_agent_key
                    resolved = conversation_start.sprint_item_supervisor_resolve(item)
                    required_sprint_item_id = target.id
                elif target.kind is PrincipalKind.chief:
                    agent_key = CHIEF_SETTINGS_KEY
                    resolved = conversation_start.agent_resolve(conn)
                    required_sprint_item_id = None
                else:
                    ticket = tickets_data.read_ticket(conn, target.id)
                    agent_key = None
                    resolved = conversation_start.worker_resolve(conn, ticket)
                    required_sprint_item_id = None
            except PlannerError as error:
                if error.code is ErrorCode.not_found:
                    data.close_undeliverable_batch(conn, batch.id, now=now())
                    return False
                raise

            link_key = f"ticket:{target.id}" if agent_key is None else f"agent:{agent_key}"
            async with conversation_start.conversation_link_lock(link_key):
                current = (
                    ticket.conversation_id
                    if agent_key is None
                    else conversation_start.read_agent_conversation(conn, agent_key)
                )
                if current is not None and await conversation_system.is_running(current):
                    data.release_pending_batch(conn, batch.id, process_token=process_token)
                    return False
                created = conversation_start.new_conversation_id()
                intended_conversation_id = current or created
                data.record_batch_offering(
                    conn,
                    batch.id,
                    conversation_id=intended_conversation_id,
                    process_token=process_token,
                    now=now(),
                )
                try:
                    if agent_key is None:
                        delivered = await conversation_start.send_to_ticket_conversation(
                            conversation_system,
                            conn,
                            target.id,
                            text_message_content(batch.message),
                            conversation_id=current,
                            created_conversation_id=created,
                            sender_label=WAKE_SENDER_LABEL,
                            mode=PromptDeliveryMode.queue,
                            sender_message_id=batch.sender_message_id,
                            reply_requested=False,
                            now=now(),
                        )
                    else:
                        delivered = await conversation_start.send_to_agent_conversation(
                            conversation_system,
                            conn,
                            agent_key,
                            text_message_content(batch.message),
                            resolved,
                            conversation_id=current,
                            created_conversation_id=created,
                            sender_label=WAKE_SENDER_LABEL,
                            mode=PromptDeliveryMode.queue,
                            sender_message_id=batch.sender_message_id,
                            reply_requested=False,
                            required_sprint_item_id=required_sprint_item_id,
                        )
                except BaseException:
                    data.record_batch_uncertain(
                        conn,
                        batch.id,
                        conversation_id=intended_conversation_id,
                        process_token=process_token,
                        now=now(),
                    )
                    raise
                if isinstance(delivered.fate, PromptDeliveryRefused):
                    data.record_batch_failure(
                        conn,
                        batch.id,
                        status=WakeBatchStatus.refused,
                        conversation_id=delivered.conversation_id,
                        now=now(),
                    )
                    return False
                if isinstance(delivered.fate, PromptDeliveryUncertain):
                    data.record_batch_uncertain(
                        conn,
                        batch.id,
                        conversation_id=delivered.conversation_id
                        or intended_conversation_id,
                        process_token=process_token,
                        now=now(),
                    )
                    return False
                assert delivered.conversation_id is not None
                data.record_batch_accepted(
                    conn,
                    batch.id,
                    conversation_id=delivered.conversation_id,
                    process_token=process_token,
                    now=now(),
                )
                reconcile_batch_outcomes(conn, now=now())
                return True

    finally:
        conn.close()


class ManagerWakeLoop:
    """Poll durable wakes and schedule one delivery task per target principal."""

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
        self._process_token = uuid4().hex
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._in_flight_lock = threading.Lock()
        self._in_flight: dict[concurrent.futures.Future[bool], int] = {}

    def wake(self) -> None:
        self._wake.set()

    def poll_once(self) -> list[int]:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            now = self._clock.now_unix()
            reconcile_batch_outcomes(conn, now=now)
            data.preserve_interrupted_dispatches(
                conn, process_token=self._process_token, now=now
            )
            data.recover_accepted_batches_from_other_processes(
                conn, process_token=self._process_token, now=now
            )
            batches = list(data.pending_batches(conn))
            attempted_targets = {batch.target for batch in batches}
            while True:
                batch = data.claim_next_batch(
                    conn, process_token=self._process_token, now=now
                )
                if batch is None:
                    break
                if batch.target in attempted_targets:
                    break
                attempted_targets.add(batch.target)
                batches.append(batch)
        finally:
            conn.close()
        return [batch.id for batch in batches if self._schedule(batch)]

    def _schedule(self, batch: WakeBatch) -> bool:
        with self._in_flight_lock:
            if self._stop.is_set() or batch.id in self._in_flight.values():
                return False
        future = asyncio.run_coroutine_threadsafe(
            deliver_batch(
                batch,
                connect_database=lambda: connect(self._db_path, self._busy_timeout_ms),
                conversation_system=self._conversation_system,
                process_token=self._process_token,
                now=self._clock.now_unix,
            ),
            self._asyncio_loop,
        )
        with self._in_flight_lock:
            self._in_flight[future] = batch.id
        future.add_done_callback(self._delivery_ended)
        return True

    def _delivery_ended(self, future: concurrent.futures.Future[bool]) -> None:
        with self._in_flight_lock:
            batch_id = self._in_flight.pop(future, None)
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            _LOG.error(
                "manager wake delivery ended in an uncertain failure (batch=%s)",
                batch_id,
                exc_info=error,
            )

    def start(self, interval: int) -> None:
        if self._thread is not None:
            raise RuntimeError("manager wake loop already started")
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="manager-wake-loop",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, deadline: float | None = None) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            timeout = 10.0 if deadline is None else max(0.0, deadline - monotonic())
            thread.join(timeout=timeout)
            self._thread = None
        with self._in_flight_lock:
            in_flight = tuple(self._in_flight)
        if in_flight:
            _, still_going = concurrent.futures.wait(
                in_flight,
                timeout=10.0 if deadline is None else max(0.0, deadline - monotonic()),
            )
            for future in still_going:
                future.cancel()

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _LOG.exception("manager wake poll failed")
            self._wake.wait(interval)
            self._wake.clear()
