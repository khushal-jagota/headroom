"""Accept and execute one employee step for a Ticket.

The runner owns prompt construction, the Ticket claim, the worker Hermes session,
Panels worker Chat state, and settlement. Automatic readiness discovery is a
separate responsibility in :mod:`planner.runtime.ticket_readiness_loop`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading

from planner.chat import service as chat_service
from planner.core.clock import Clock
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic import dates
from planner.minds.shared_gateway import SharedGateway, SharedGatewayBusy
from planner.runtime import readiness
from planner.runtime.readiness_doorbell import ReadinessDoorbell
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket, TicketStatus
from planner.tickets.logic import coding_bridge, machine

_log = logging.getLogger(__name__)

_REVISION_GUIDANCE_PREFIX = (
    "The user rejected your proposal and provided the following guidance:"
)


class _WorkerSessionClaimLost(Exception):
    """The Ticket stopped owning the worker session before prompt submission."""


def _next_step_prompt(ticket: Ticket) -> str:
    """Describe what to advance; the worker role skill owns how to do the work.

    Route selection/suitability guidance lives in the panels-worker skill, not here.
    The gating field is resolved against the ticket's OWN type definition (not the
    coding default), so a novel-stage type (e.g. new_worker at needs_stages) reads
    its real field instead of raising 'stage outside the linear order'."""
    defn = coding_bridge.require(ticket.worker_type)
    gating = machine.gating_field(ticket.stage, definition=defn)
    field = str(gating) if gating is not None else "the next step"
    implementer_wire = ticket.implementer.value if ticket.implementer is not None else "unassigned"
    return (
        f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
        f"take the next step and propose the '{field}' field for approval. "
        f"Implementer: {implementer_wire}."
    )


class _EmployeeRevisionHandoff:
    def __init__(self) -> None:
        self._parked = threading.Event()
        self._decision = threading.Event()
        self._choice: bool | None = None
        self._choice_lock = threading.Lock()

    def release(self) -> None:
        self._choose(True)

    def cancel(self) -> None:
        self._choose(False)

    def _choose(self, release: bool) -> None:
        with self._choice_lock:
            if self._choice is None:
                self._choice = release
                self._decision.set()

    def _mark_parked(self) -> None:
        self._parked.set()

    def _wait_until_parked(self) -> None:
        self._parked.wait()

    def _wait_for_decision(self) -> bool:
        self._decision.wait()
        with self._choice_lock:
            assert self._choice is not None
            return self._choice


class EmployeeStepRunner:
    """Lifecycle owner for automatic and directly revised employee turns."""

    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        gateway: SharedGateway,
        readiness_doorbell: ReadinessDoorbell,
        boundary_hour: int,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._gateway = gateway
        self._readiness_doorbell = readiness_doorbell
        self._boundary_hour = boundary_hour
        self._busy_timeout_ms = busy_timeout_ms
        self._accepting = True
        self._active = 0
        self._active_cond = threading.Condition()

    def run_ready_step(self, ticket_id: str) -> None:
        """Start one automatic step; the transaction-time readiness check is final."""
        with self._active_cond:
            if not self._accepting:
                return
            self._active += 1
            thread = threading.Thread(
                target=self._run_ready_thread,
                args=(ticket_id,),
                name=f"employee-step-{ticket_id}",
                daemon=True,
            )
            try:
                thread.start()
            except BaseException:
                self._active -= 1
                self._active_cond.notify_all()
                raise

    def reserve_revision(
        self, ticket_id: str, guidance: str
    ) -> _EmployeeRevisionHandoff:
        """Return only after a counted employee thread is parked for this revision."""
        with self._active_cond:
            if not self._accepting or not self._gateway_available():
                raise PlannerError(
                    ErrorCode.gateway_offline,
                    "employee runner is unavailable",
                    {"ticket_id": ticket_id},
                )
            handoff = _EmployeeRevisionHandoff()
            self._active += 1
            thread = threading.Thread(
                target=self._run_reserved_revision,
                args=(ticket_id, guidance, handoff),
                name=f"employee-revision-{ticket_id}",
                daemon=True,
            )
            try:
                thread.start()
            except BaseException:
                self._active -= 1
                self._active_cond.notify_all()
                raise
        handoff._wait_until_parked()
        return handoff

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait for every accepted automatic run or revision reservation."""
        with self._active_cond:
            return self._active_cond.wait_for(lambda: self._active == 0, timeout)

    def stop(self) -> None:
        """Close admission and drain every accepted run before returning."""
        with self._active_cond:
            self._accepting = False
            self._active_cond.wait_for(lambda: self._active == 0)

    def _gateway_available(self) -> bool:
        try:
            return self._gateway.status().available
        except Exception:  # an unavailable adapter must reject before Ticket mutation
            return False

    def _run_ready_thread(self, ticket_id: str) -> None:
        settled = False
        try:
            settled = self._run(ticket_id, revision_guidance=None)
        finally:
            self._finish_active(settled)

    def _run_reserved_revision(
        self,
        ticket_id: str,
        guidance: str,
        handoff: _EmployeeRevisionHandoff,
    ) -> None:
        handoff._mark_parked()
        settled = False
        try:
            if handoff._wait_for_decision():
                settled = self._run(ticket_id, revision_guidance=guidance)
        finally:
            self._finish_active(settled)

    def _finish_active(self, settled: bool) -> None:
        with self._active_cond:
            self._active -= 1
            if settled:
                self._readiness_doorbell.ring()
            self._active_cond.notify_all()

    def _run(self, ticket_id: str, *, revision_guidance: str | None) -> bool:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            now = self._clock.now_unix()
            if revision_guidance is None:
                def ready_on_today(
                    _conn: sqlite3.Connection, ticket: Ticket
                ) -> bool:
                    today_id = dates.resolve_day_id(
                        "today", self._clock.now(), self._boundary_hour
                    )
                    on_today = _conn.execute(
                        "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
                        (today_id, ticket.id),
                    ).fetchone()
                    return on_today is not None and readiness.is_runnable(_conn, ticket)

                claimed = tickets_data.start_run_if_runnable(
                    conn,
                    ticket_id,
                    guard=ready_on_today,
                    now=now,
                )
                if claimed is None:
                    _log.info(
                        "employee runner skipped a no-longer-ready Ticket (ticket=%s)",
                        ticket_id,
                    )
                    return False
                prompt = _next_step_prompt(claimed)
                show_prompt_in_chat = True
                require_existing_session = False
            else:
                claimed = tickets_data.read_ticket(conn, ticket_id)
                if claimed.ticket_status is not TicketStatus.agent_running_step:
                    return False
                if claimed.chat_session_key is None:
                    tickets_data.mark_run_errored_if_still_running_step(
                        conn,
                        ticket_id,
                        error="claimed employee revision has no existing session",
                        now=now,
                    )
                    return True
                prompt = f"{_REVISION_GUIDANCE_PREFIX}\n\n{revision_guidance}"
                show_prompt_in_chat = False
                require_existing_session = True

            current_session_key = claimed.chat_session_key
            try:
                worker_turn = chat_service.start_worker_turn(
                    conn,
                    ticket_id,
                    visible_text=prompt if show_prompt_in_chat else "",
                    now=now,
                )
            except PlannerError as exc:
                if exc.code is ErrorCode.already_running:
                    tickets_data.mark_run_errored_if_still_running_step(
                        conn,
                        ticket_id,
                        error="employee worker turn collided with an active chat turn",
                        now=self._clock.now_unix(),
                    )
                    return True
                raise

            def persist_session_key(session_key: str) -> None:
                nonlocal current_session_key
                event_now = self._clock.now_unix()
                updated = tickets_data.claim_running_step_chat_session_key(
                    conn,
                    ticket_id,
                    session_key=session_key,
                    now=event_now,
                )
                if (
                    updated.ticket_status is not TicketStatus.agent_running_step
                    or updated.chat_session_key != session_key
                ):
                    raise _WorkerSessionClaimLost
                current_session_key = updated.chat_session_key
                chat_service.attach_worker_session_key(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    session_key,
                    event_now,
                )

            def observe_gateway_event(event: dict[str, object]) -> None:
                chat_service.observe_worker_gateway_event(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    event,
                    self._clock.now_unix(),
                )

            def finish_running_step(session_key: str | None) -> None:
                if session_key is None:
                    tickets_data.finish_run_if_still_running_step(
                        conn, ticket_id, now=now
                    )
                else:
                    tickets_data.finish_run_if_still_running_step(
                        conn,
                        ticket_id,
                        session_key=session_key,
                        now=now,
                    )

            def mark_errored(error: str, session_key: str | None) -> None:
                if session_key is None:
                    tickets_data.mark_run_errored_if_still_running_step(
                        conn,
                        ticket_id,
                        error=error,
                        now=now,
                    )
                else:
                    tickets_data.mark_run_errored_if_still_running_step(
                        conn,
                        ticket_id,
                        error=error,
                        session_key=session_key,
                        now=now,
                    )

            try:
                if require_existing_session:
                    result = self._gateway.run_ticket_step(
                        claimed.chat_session_key,
                        ticket_id,
                        prompt,
                        observe_gateway_event,
                        on_session_key=persist_session_key,
                        require_existing_session=True,
                    )
                else:
                    result = self._gateway.run_ticket_step(
                        claimed.chat_session_key,
                        ticket_id,
                        prompt,
                        observe_gateway_event,
                        on_session_key=persist_session_key,
                    )
            except SharedGatewayBusy as exc:
                chat_service.fail_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    "session busy",
                    self._clock.now_unix(),
                )
                finish_running_step(exc.session_key or current_session_key)
                return True
            except _WorkerSessionClaimLost:
                _log.info(
                    "employee runner skipped an unowned worker session (ticket=%s)",
                    ticket_id,
                )
                chat_service.fail_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    "worker session ownership was lost",
                    self._clock.now_unix(),
                )
                finish_running_step(current_session_key)
                return True
            except Exception as exc:  # never leave the Ticket at agent_running_step
                _log.exception("employee step crashed (ticket=%s)", ticket_id)
                error = f"employee step crashed: {exc}"
                chat_service.fail_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    error,
                    self._clock.now_unix(),
                )
                mark_errored(error, current_session_key)
                return True

            current_session_key = result.session_key or current_session_key
            if result.status == "complete":
                chat_service.finish_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    result.text,
                    "complete",
                    self._clock.now_unix(),
                )
                finish_running_step(current_session_key)
            elif result.status == "interrupted":
                chat_service.finish_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    result.text,
                    "interrupted",
                    self._clock.now_unix(),
                )
                mark_errored("run interrupted", current_session_key)
            else:
                error = result.error or "gateway run failed"
                chat_service.fail_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    error,
                    self._clock.now_unix(),
                )
                mark_errored(error, current_session_key)
            return True
        finally:
            conn.close()
