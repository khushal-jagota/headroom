"""Recover and deliver durable proposal-holder wakes."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sqlite3
import threading
from time import monotonic

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
    PromptDeliveryUncertain,
)
from planner.core.clock import Clock
from planner.core.db import connect
from planner.message_delivery import service as message_delivery_service
from planner.proposal_holder_wakes import data
from planner.tickets import data as tickets_data

_LOG = logging.getLogger(__name__)


async def _deliver_pending_wakes(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    ticket_id: str | None = None,
    retry_delay_seconds: int = 1,
) -> int:
    """Attempt due wakes; deterministic message ids make every replay idempotent."""
    now = clock.now_unix()
    data.reconcile_missing(conn, now=now)
    delivered_count = 0
    for wake in data.claim_due(
        conn,
        now=now,
        ticket_id=ticket_id,
    ):
        ticket = tickets_data.read_ticket(conn, wake.ticket_id)
        if ticket.pending_proposal is None or ticket.ceiling_holder != wake.holder:
            data.cancel_claimed(conn, wake, now=now)
            continue
        try:
            result = await message_delivery_service.send_system_message(
                conversations,
                conn,
                clock,
                wake.holder,
                wake.message,
                sender_message_id=wake.sender_message_id,
            )
        except Exception as error:
            data.record_exception(
                conn,
                wake,
                error=str(error),
                retry_at=now + retry_delay_seconds,
                now=now,
            )
            continue
        if isinstance(result.fate, PromptDeliveryRefused):
            data.record_refusal(
                conn,
                wake,
                error=result.fate.refusal_reason.value,
                retry_at=now + retry_delay_seconds,
                now=now,
            )
        elif isinstance(result.fate, PromptDeliveryQueued):
            # The held queue is process-local. Keep the claim and replay its stable ID:
            # a live queue still says queued, durable delivery says started, and restart
            # resets the claim so the lost queue can be recreated.
            continue
        elif isinstance(result.fate, PromptDeliveryUncertain):
            data.mark_uncertain(
                conn,
                wake,
                error="conversation delivery outcome is uncertain; automatic retry disabled",
                now=now,
            )
        elif data.mark_delivered(conn, wake, now=now):
            delivered_count += 1
    if ticket_id is not None:
        delivered_count += await _deliver_rejection_messages(
            conversations,
            conn,
            clock,
            ticket_id=ticket_id,
            retry_delay_seconds=retry_delay_seconds,
        )
    return delivered_count


async def _deliver_rejection_messages(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    ticket_id: str,
    retry_delay_seconds: int,
) -> int:
    delivered_count = 0
    while True:
        now = clock.now_unix()
        message = data.claim_due_rejection_message(conn, ticket_id=ticket_id, now=now)
        if message is None:
            return delivered_count
        try:
            result = await message_delivery_service.send_ticket_outbox_message(
                conversations,
                conn,
                clock,
                ticket_id,
                message.message,
                sender=message.sender,
                sender_message_id=message.sender_message_id,
            )
        except Exception as error:
            data.settle_rejection_message(
                conn,
                message,
                state="pending",
                retry_at=now + retry_delay_seconds,
                error=str(error),
                now=now,
            )
            return delivered_count
        if isinstance(result.fate, PromptDeliveryRefused):
            data.settle_rejection_message(
                conn,
                message,
                state="pending",
                retry_at=now + retry_delay_seconds,
                error=result.fate.refusal_reason.value,
                advance_attempt=True,
                now=now,
            )
            return delivered_count
        if isinstance(result.fate, PromptDeliveryQueued):
            return delivered_count
        if isinstance(result.fate, PromptDeliveryUncertain):
            data.settle_rejection_message(
                conn,
                message,
                state="uncertain",
                error="conversation delivery outcome is uncertain; automatic retry disabled",
                now=now,
            )
            continue
        if data.settle_rejection_message(
            conn, message, state="delivered", now=now
        ):
            delivered_count += 1


class ProposalHolderWakeLoop:
    """Poll durable wakes on a thread and deliver each Ticket on the server loop."""

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
        self._in_flight: dict[concurrent.futures.Future[int], str] = {}

    def wake(self) -> None:
        self._wake.set()

    def _due_ticket_ids(self) -> tuple[str, ...]:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            data.reconcile_missing(conn, now=self._clock.now_unix())
            return data.due_ticket_ids(conn, now=self._clock.now_unix())
        finally:
            conn.close()

    async def _deliver_one(self, ticket_id: str) -> int:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            return await _deliver_pending_wakes(
                self._conversation_system,
                conn,
                self._clock,
                ticket_id=ticket_id,
            )
        finally:
            conn.close()

    def poll_once(self) -> tuple[str, ...]:
        return tuple(ticket_id for ticket_id in self._due_ticket_ids() if self._schedule(ticket_id))

    def _schedule(self, ticket_id: str) -> bool:
        with self._in_flight_lock:
            if self._stop.is_set() or ticket_id in self._in_flight.values():
                return False
            future = asyncio.run_coroutine_threadsafe(
                self._deliver_one(ticket_id), self._asyncio_loop
            )
            self._in_flight[future] = ticket_id
        future.add_done_callback(self._delivery_ended)
        return True

    def _delivery_ended(self, future: concurrent.futures.Future[int]) -> None:
        with self._in_flight_lock:
            ticket_id = self._in_flight.pop(future, None)
        if not future.cancelled() and future.exception() is not None:
            _LOG.error(
                "proposal holder wake task failed (ticket=%s)",
                ticket_id,
                exc_info=future.exception(),
            )

    def start(self, interval: int) -> None:
        if self._thread is not None:
            raise RuntimeError("proposal-holder wake loop already started")
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="proposal-holder-wake-loop",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, deadline: float | None = None) -> bool:
        """Stop polling and report whether every scheduled delivery settled.

        A timed-out delivery is cancelled, but cancellation completion belongs to the
        server event loop. The caller must retain the machine lock when this returns
        false, so no replacement executor can reach the same backend before process exit.
        """
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            timeout = 10.0 if deadline is None else max(0.0, deadline - monotonic())
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                return False
            self._thread = None
        with self._in_flight_lock:
            in_flight = tuple(self._in_flight)
        if in_flight:
            _, pending = concurrent.futures.wait(
                in_flight,
                timeout=10.0 if deadline is None else max(0.0, deadline - monotonic()),
            )
            for future in pending:
                future.cancel()
            if pending:
                return False
        # A queued prompt is process-local, but its scheduling future is complete.
        # Its durable claim therefore remains the final proof that this process still
        # owns work which can reach a backend.
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            return not data.has_delivering(conn)
        except Exception:
            _LOG.exception("proposal holder wake shutdown state check failed")
            return False
        finally:
            conn.close()

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _LOG.exception("proposal holder wake poll failed")
            self._wake.wait(interval)
            self._wake.clear()
