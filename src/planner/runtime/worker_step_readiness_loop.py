"""Find the Tickets that are ready for a worker step, and start one for each.

Two shapes live here. ``start_ready_worker_step`` is the whole per-Ticket flow — check,
claim, start a conversation if there is none, compose the opener, send it, and handle the
delivery's fate. It is a plain async function so it can be driven directly, one Ticket at
a time, with no threads involved.

``WorkerStepReadinessLoop`` is the thread that keeps asking. It polls on a timer, wakes
early when anything commits, and hands each ready Ticket to the flow as an independent
task on the server's event loop. It does not wait for one Ticket's send before starting
the next: the contract puts no time bound on a delivery, so one slow backend must not
hold up every other Ticket or the next poll.

Nothing here watches a turn end. A worker step is started, and the Ticket moves again
only when someone acts on it.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sqlite3
import threading
from collections.abc import Callable
from time import monotonic as _monotonic
from typing import Final
from uuid import uuid4

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
)
from planner.conversation.message_content import text_message_content
from planner.core.clock import Clock
from planner.core.db import connect
from planner.days.logic import dates
from planner.runtime import conversation_start, worker_step_readiness
from planner.runtime.logic.worker_step_prompt import compose_worker_step_prompt
from planner.skill_versions import (
    bind_worker_step_skills,
    delete_worker_step_skill_bindings,
    settle_worker_step_skill_bindings,
)
from planner.tickets import data as tickets_data
from planner.tickets import revision_feedback
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.registry import WorkerTypeRegistry

_log = logging.getLogger(__name__)

LOOP_SENDER_LABEL: Final = "loop"

_CANDIDATE_SQL = (
    "SELECT t.id FROM tickets t JOIN day_tickets dt ON dt.ticket_id = t.id WHERE dt.day_id = ? "
    "ORDER BY t.updated_at, t.id"
)


async def start_ready_worker_step(
    ticket_id: str,
    *,
    connect_database: Callable[[], sqlite3.Connection],
    conversation_system: ConversationSystem,
    worker_type_registry: WorkerTypeRegistry,
    planning_day_id_resolver: Callable[[], str],
    now: Callable[[], int],
) -> bool:
    """Start one worker step for this Ticket. Reports whether the send got anywhere.

    Every check happens before anything is sent: an occupied worker is skipped outright,
    and the claim re-runs the readiness decision under the write lock. Once the send
    reports started or queued the delivery cannot be taken back, so the claim is given
    back only when the send was refused, or when something failed before a fate existed.
    """
    conn = connect_database()
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        conversation_id = ticket.conversation_id
        if conversation_id is not None and await conversation_system.is_running(conversation_id):
            # An occupied worker is left alone for this pass. Queueing stays the answer
            # only for a collision that slipped between this check and the send.
            return False

        claimed = tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket_id,
            planning_day_id_resolver=planning_day_id_resolver,
            readiness_check=worker_step_readiness.is_ready_for_worker_step,
            now=now(),
        )
        if claimed is None:
            return False
        departure_status = claimed.ticket_status
        departure_status_revision = claimed.ticket_status_revision
        paired_opener = (
            conn.execute(
                "SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id = ? AND stage = ?",
                (ticket_id, claimed.stage),
            ).fetchone()
            is not None
        )
        sender_message_id = f"worker_step_message_{uuid4().hex}"

        def give_the_claim_back(*, opener_succeeded: bool = False) -> None:
            released = tickets_data.release_worker_step_claim(
                conn,
                ticket_id,
                expected_status=departure_status,
                expected_status_revision=departure_status_revision,
                now=now(),
            )
            if released and paired_opener and not opener_succeeded:
                tickets_data.forget_user_stage_opener(
                    conn,
                    ticket_id,
                    stage=claimed.stage,
                )

        def remove_tentative_bindings() -> None:
            delete_worker_step_skill_bindings(conn, sender_message_id)

        try:
            # Inside the guard: a lookup that raises must give the claim back, or the
            # Ticket sits out of `empty` with nothing running and nothing to re-claim it.
            worker_type_definition = worker_type_registry.require(claimed.worker_type)
            bind_worker_step_skills(
                conn,
                sender_message_id,
                worker_type_definition.worker_profile.specialist_skill,
            )
            conversation_id = claimed.conversation_id
            # Composed before anything is made: a failure here must not leave a conversation
            # behind, and the opener is what would have brought one into being.
            current_ticket = tickets_data.read_ticket(conn, ticket_id)
            pending_revision_feedback = revision_feedback.snapshot(conn, ticket_id)
            prompt = compose_worker_step_prompt(
                current_ticket,
                worker_type_definition=worker_type_definition,
                revision_feedback=pending_revision_feedback,
            )
            delivered = await conversation_start.send_to_ticket_conversation(
                conversation_system,
                conn,
                ticket_id,
                text_message_content(prompt.model_text),
                conversation_id=conversation_id,
                created_conversation_id=conversation_start.new_conversation_id(),
                sender_label=LOOP_SENDER_LABEL,
                mode=PromptDeliveryMode.queue,
                sender_message_id=sender_message_id,
                worker_type_registry=worker_type_registry,
                now=now(),
            )
            conversation_id = delivered.conversation_id
            fate = delivered.fate
        except Exception:
            _log.exception(
                "worker step could not be started (ticket=%s conversation=%s)",
                ticket_id,
                conversation_id,
            )
            remove_tentative_bindings()
            give_the_claim_back()
            return False

        if isinstance(fate, PromptDeliveryRefused):
            # The refusal is on the record before the claim is given back, so a release
            # that itself fails cannot swallow the one line that says why nothing ran.
            _log.error(
                "worker step send was refused (ticket=%s conversation=%s reason=%s)",
                ticket_id,
                conversation_id,
                fate.refusal_reason.value,
            )
            remove_tentative_bindings()
            give_the_claim_back()
            return False

        if not isinstance(fate, PromptDeliveryQueued):
            # The durable conversation record normally finalizes this in the same
            # transaction as its prompt row. This idempotent update also supports the
            # in-memory conversation system used by focused worker-step tests.
            settle_worker_step_skill_bindings(conn, sender_message_id, delivered=True)

        if paired_opener:
            give_the_claim_back(opener_succeeded=True)

        if pending_revision_feedback is not None:
            try:
                revision_feedback.acknowledge(
                    conn,
                    ticket_id,
                    pending_revision_feedback.revision,
                )
            except Exception:
                # The text is delivered and cannot be taken back, so this is reported and
                # nothing is reverted: reverting would only send the feedback twice.
                _log.exception(
                    "delivered revision feedback could not be acknowledged (ticket=%s)",
                    ticket_id,
                )
        return True
    finally:
        conn.close()


class WorkerStepReadinessLoop:
    """Poll for Tickets ready for a worker step and start one for each, in parallel."""

    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        conversation_system: ConversationSystem,
        asyncio_loop: asyncio.AbstractEventLoop,
        boundary_hour: int,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._conversation_system = conversation_system
        self._asyncio_loop = asyncio_loop
        self._boundary_hour = boundary_hour
        self._busy_timeout_ms = busy_timeout_ms
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._in_flight_lock = threading.Lock()
        self._in_flight: dict[concurrent.futures.Future[bool], str] = {}

    def wake(self) -> None:
        """Ask the loop to poll now; the periodic timer remains the backstop."""
        self._wake.set()

    def _planning_day_id(self) -> str:
        return dates.resolve_day_id("today", self._clock.now(), self._boundary_hour)

    def _ready_ticket_ids(self, planning_day_id: str) -> list[str]:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            rows = conn.execute(_CANDIDATE_SQL, (planning_day_id,)).fetchall()
            ready_ticket_ids: list[str] = []
            claimed_closeout_lanes: set[worker_step_readiness.CloseoutLaneIdentity] = set()
            registry = configured_worker_type_registry()
            for row in rows:
                ticket = tickets_data.read_ticket(conn, str(row["id"]))
                worker_type_definition = registry.require(ticket.worker_type)
                if not worker_step_readiness.is_ready_for_worker_step(
                    conn,
                    ticket,
                    planning_day_id=planning_day_id,
                    worker_type_definition=worker_type_definition,
                ):
                    continue
                closeout_lane = worker_step_readiness.closeout_lane_identity(
                    conn,
                    ticket,
                    worker_type_definition=worker_type_definition,
                )
                if closeout_lane is not None:
                    # One Closeout lane takes one Ticket per pass. The lane looks free to
                    # every waiter until one of them actually flips, so without this the
                    # same pass would send several into the same lane.
                    if closeout_lane in claimed_closeout_lanes:
                        continue
                    claimed_closeout_lanes.add(closeout_lane)
                ready_ticket_ids.append(ticket.id)
            return ready_ticket_ids
        finally:
            conn.close()

    def poll_once(self) -> list[str]:
        """Start a worker step for every ready Ticket. Returns the ids it set going."""
        planning_day_id = self._planning_day_id()
        return [
            ticket_id
            for ticket_id in self._ready_ticket_ids(planning_day_id)
            if self._schedule(ticket_id)
        ]

    def _schedule(self, ticket_id: str) -> bool:
        with self._in_flight_lock:
            if self._stop.is_set():
                return False
            if ticket_id in self._in_flight.values():
                return False
        future = asyncio.run_coroutine_threadsafe(
            start_ready_worker_step(
                ticket_id,
                connect_database=lambda: connect(self._db_path, self._busy_timeout_ms),
                conversation_system=self._conversation_system,
                worker_type_registry=configured_worker_type_registry(),
                planning_day_id_resolver=self._planning_day_id,
                now=self._clock.now_unix,
            ),
            self._asyncio_loop,
        )
        with self._in_flight_lock:
            self._in_flight[future] = ticket_id
        future.add_done_callback(self._step_ended)
        return True

    def _step_ended(self, future: concurrent.futures.Future[bool]) -> None:
        """Forget a finished step, and say so when it ended in a way nothing else saw.

        The flow handles its own failures, but the ground it stands on can give way
        underneath it — the database connection, the handling of a failure, the close on
        the way out. Nobody awaits these tasks, so an exception that gets this far has no
        other way to be heard.
        """
        with self._in_flight_lock:
            ticket_id = self._in_flight.pop(future, None)
        if future.cancelled():
            # Stopping cancels what it could not wait out. That is a decision, not a fault.
            return
        error = future.exception()
        if error is not None:
            _log.error(
                "worker step task ended in an unhandled failure (ticket=%s)",
                ticket_id,
                exc_info=error,
            )

    def start(self, interval: int) -> None:
        """Start the polling thread."""
        if self._thread is not None:
            raise RuntimeError("worker-step readiness loop already started")
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="worker-step-readiness-loop",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, deadline: float | None = None) -> None:
        """Stop polling, wait out the steps in flight, and cancel whatever outlasts that.

        A step that is still going at the deadline is cancelled rather than left running:
        the caller releases the machine lock the moment this returns, and another process
        may pick the work up. Cancelling is best-effort — it reaches a step that is
        waiting on something, and a step past the point of no return finishes anyway.

        A cancelled step leaves its Ticket at its departure status with no live
        conversation behind it. That mismatch is the honest record of a process that
        stopped mid-step: ``is_running`` is the live answer, and no machinery pretends
        otherwise.
        """
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

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _log.exception("worker-step readiness poll failed")
            self._wake.wait(interval)
            self._wake.clear()


def _remaining(deadline: float | None) -> float:
    return 10.0 if deadline is None else max(0.0, deadline - _monotonic())
