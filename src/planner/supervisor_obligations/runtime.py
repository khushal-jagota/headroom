"""Single-owner delivery loop for Sprint Item supervisor obligations."""

from __future__ import annotations

import asyncio
import logging
import threading
from time import monotonic

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
)
from planner.conversation.message_content import text_message_content
from planner.core.clock import Clock
from planner.core.db import connect
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.supervisor_obligations import data

_LOGGER = logging.getLogger(__name__)
MAXIMUM_ATTEMPTS = 5
DELIVERY_TIMEOUT_SECONDS = 30


class SupervisorObligationLoop:
    """Reconcile and deliver one bounded obligation batch at a time."""

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
        self._interval = 1.0

    def start(self, interval: float) -> None:
        self._interval = interval
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="supervisor-obligations"
        )
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, *, deadline: float | None = None) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            timeout = None if deadline is None else max(0.1, deadline - monotonic())
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise RuntimeError("supervisor obligation loop did not stop before the deadline")

    def _run(self) -> None:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            self._recover_after_restart(conn)
            while not self._stop.is_set():
                try:
                    self.run_once(conn)
                except Exception:
                    _LOGGER.exception("Supervisor obligation delivery failed")
                self._wake.wait(self._interval)
                self._wake.clear()
        finally:
            conn.close()

    def run_once(self, conn=None) -> bool:
        owns = conn is None
        if conn is None:
            conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            now = self._clock.now_unix()
            data.reconcile(conn, now)
            data.reconcile_deliveries(conn, now)
            claimed = data.prepared_delivery(conn) or data.claim_batch(conn, now)
            if claimed is None:
                return False
            delivery, obligations = claimed
            text = _batch_text(obligations)
            future = asyncio.run_coroutine_threadsafe(
                self._deliver(delivery, text),
                self._asyncio_loop,
            )
            try:
                result = future.result(timeout=DELIVERY_TIMEOUT_SECONDS)
            except Exception as exc:
                future.cancel()
                terminal = (
                    max(obligation.attempt_count + 1 for obligation in obligations)
                    >= MAXIMUM_ATTEMPTS
                )
                data.settle_delivery(
                    conn,
                    delivery.id,
                    state="failed" if terminal else "refused",
                    now=now,
                    error=str(exc),
                    terminal=terminal,
                )
                return True
            if isinstance(result.fate, PromptDeliveryRefused):
                terminal = (
                    max(obligation.attempt_count + 1 for obligation in obligations)
                    >= MAXIMUM_ATTEMPTS
                )
                data.settle_delivery(
                    conn,
                    delivery.id,
                    state="failed" if terminal else "refused",
                    now=now,
                    conversation_id=result.conversation_id,
                    error=result.fate.refusal_reason.value,
                    terminal=terminal,
                )
            else:
                data.settle_delivery(
                    conn,
                    delivery.id,
                    state="queued"
                    if isinstance(result.fate, PromptDeliveryQueued)
                    else "delivered",
                    now=now,
                    conversation_id=result.conversation_id,
                )
            return True
        finally:
            if owns:
                conn.close()

    def _recover_after_restart(self, conn) -> None:
        """Recover only queues that belonged to the previous process."""
        now = self._clock.now_unix()
        data.reconcile_deliveries(conn, now)
        rows = conn.execute(
            "SELECT id,conversation_id,sender_message_id FROM "
            "supervisor_obligation_deliveries WHERE state='queued'"
        ).fetchall()
        for row in rows:
            conversation_id = (
                None if row["conversation_id"] is None else str(row["conversation_id"])
            )
            held = (
                ()
                if conversation_id is None
                else asyncio.run_coroutine_threadsafe(
                    self._conversation_system.held_prompts(conversation_id), self._asyncio_loop
                ).result()
            )
            if not any(
                prompt.sender_message_id == str(row["sender_message_id"]) for prompt in held
            ):
                data.mark_queued_outcome_uncertain(conn, str(row["id"]), now)

    async def _deliver(self, delivery, text: str):
        """Resolve the current lazy supervisor and send under its deletion lock."""
        async with sprints_service.supervisor_lifecycle_lock(delivery.sprint_item_id):
            conn = connect(self._db_path, self._busy_timeout_ms)
            try:
                item = sprints_data.read_item(conn, delivery.sprint_item_id).item
                current = conversation_start.read_agent_conversation(
                    conn, item.supervisor_agent_key
                )
                target = current or conversation_start.new_conversation_id()
                data.mark_dispatching(conn, delivery.id, target, self._clock.now_unix())
                return await conversation_start.send_to_agent_conversation(
                    self._conversation_system,
                    conn,
                    item.supervisor_agent_key,
                    text_message_content(text),
                    conversation_start.sprint_item_supervisor_resolve(item),
                    conversation_id=current,
                    created_conversation_id=target,
                    sender_label="Panels",
                    sender_message_id=delivery.sender_message_id,
                    required_sprint_item_id=item.id,
                )
            finally:
                conn.close()


def _batch_text(obligations) -> str:
    lines = ["Sprint Item supervisor obligations:"]
    for obligation in obligations:
        lines.append(
            f"- {obligation.id}: Ticket {obligation.ticket_id} requires {obligation.kind.value}."
        )
    lines.append(
        "Read current context before acting. Acknowledge handled obligation IDs explicitly."
    )
    return "\n".join(lines)
