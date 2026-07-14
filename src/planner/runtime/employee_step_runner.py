"""Accept and execute one employee step for a Ticket.

The runner owns prompt construction, the Ticket claim, the worker Hermes session,
Panels worker Chat state, and settlement. Automatic Employee-step discovery is a
separate responsibility in
:mod:`planner.runtime.automatic_employee_step_discovery_loop`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from time import monotonic as _monotonic

from planner.chat import data as chat_data
from planner.chat import service as chat_service
from planner.core.clock import Clock
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic import dates
from planner.minds.shared_gateway import SharedGateway, SharedGatewayBusy
from planner.runtime import automatic_employee_step_eligibility
from planner.runtime.automatic_employee_step_eligibility_wake import (
    AutomaticEmployeeStepEligibilityWake,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import EmployeeSessionIdTransition, Ticket, TicketStatus
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.contracts import WorkerTypeDefinition

_log = logging.getLogger(__name__)

_REVISION_GUIDANCE_PREFIX = "The user rejected your proposal and provided the following guidance:"
_RESTART_RECOVERY_MESSAGE = (
    "Panels restarted while this ticket Employee turn was running. "
    "Resume the existing Hermes conversation for this ticket. "
    "First inspect the canonical ticket and the existing conversation. "
    "Then continue unfinished work and avoid repeating completed actions. "
    "Then file the currently requested proposal through the normal Panels worker tools. "
    "And if you already filed that proposal, only say so in Chat."
)


class _WorkerSessionClaimLost(Exception):
    """The Ticket stopped owning the worker session before prompt submission."""


@dataclass(frozen=True)
class _MatchedEmployeeSessionTurnSnapshot:
    employee_session_id: str
    ticket_id: str
    turn_id: str


def _next_step_prompt(
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> str:
    """Describe what to advance; the worker role skill owns how to do the work.

    The Worker type selects the specialist skill; this prompt carries only the current
    Stage ownership and the gated field to advance.
    The gating field is resolved against the ticket's OWN type definition (not the
    coding default), so a novel-stage type (e.g. new_worker at needs_stages) reads
    its real field instead of raising 'stage outside the linear order'."""
    gating = worker_type_definition.gating_field(ticket.stage)
    field = str(gating) if gating is not None else "the next step"

    ownership_wire = (
        ticket.effective_stage_ownership_mode.value
        if ticket.effective_stage_ownership_mode is not None
        else "terminal"
    )
    return (
        f"Work ticket {ticket.id} — {ticket.title}. It is at Stage '{str(ticket.stage)}'; "
        f"take the next step and propose the '{field}' field for approval. "
        f"Stage owner: {ownership_wire}."
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
        automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
        boundary_hour: int,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._gateway = gateway
        self._automatic_employee_step_eligibility_wake = automatic_employee_step_eligibility_wake
        self._boundary_hour = boundary_hour
        self._busy_timeout_ms = busy_timeout_ms
        self._accepting = True
        self._stopping = False
        self._active = 0
        self._active_ticket_ids: set[str] = set()
        self._active_cond = threading.Condition()

    def try_run_automatic_step(self, ticket_id: str) -> None:
        """Start one automatic step; the transaction-time eligibility check is final."""
        with self._active_cond:
            if not self._accepting:
                return
            if ticket_id in self._active_ticket_ids:
                return
            self._active_ticket_ids.add(ticket_id)
            self._active += 1
            thread = threading.Thread(
                target=self._run_automatic_step_thread,
                args=(ticket_id,),
                name=f"employee-step-{ticket_id}",
                daemon=True,
            )
            try:
                thread.start()
            except BaseException:
                self._active_ticket_ids.discard(ticket_id)
                self._active -= 1
                self._active_cond.notify_all()
                raise

    def recover_running_step(self, ticket_id: str) -> None:
        """Strictly resume one durable Employee step stranded by a prior process."""
        with self._active_cond:
            if not self._accepting:
                return
            if ticket_id in self._active_ticket_ids:
                return
            self._active_ticket_ids.add(ticket_id)
            self._active += 1
            thread = threading.Thread(
                target=self._run_recovery_thread,
                args=(ticket_id,),
                name=f"employee-recovery-{ticket_id}",
                daemon=True,
            )
            try:
                thread.start()
            except BaseException:
                self._active_ticket_ids.discard(ticket_id)
                self._active -= 1
                self._active_cond.notify_all()
                raise

    def reserve_revision(self, ticket_id: str, guidance: str) -> _EmployeeRevisionHandoff:
        """Return only after a counted employee thread is parked for this revision."""
        with self._active_cond:
            if not self._accepting or not self._gateway_available():
                raise PlannerError(
                    ErrorCode.gateway_offline,
                    "employee runner is unavailable",
                    {"ticket_id": ticket_id},
                )
            if ticket_id in self._active_ticket_ids:
                raise PlannerError(
                    ErrorCode.already_running,
                    "employee runner already owns this ticket",
                    {"ticket_id": ticket_id},
                )
            handoff = _EmployeeRevisionHandoff()
            self._active_ticket_ids.add(ticket_id)
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
                self._active_ticket_ids.discard(ticket_id)
                self._active -= 1
                self._active_cond.notify_all()
                raise
        handoff._wait_until_parked()
        return handoff

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait for every accepted automatic run or revision reservation."""
        with self._active_cond:
            return self._active_cond.wait_for(lambda: self._active == 0, timeout)

    def _remaining_shutdown_busy_timeout_ms(self, deadline: float | None) -> int:
        if deadline is None:
            return self._busy_timeout_ms
        remaining_ms = int(max(0.0, deadline - _monotonic()) * 1000)
        return min(self._busy_timeout_ms, remaining_ms)

    def _apply_remaining_shutdown_busy_timeout(
        self,
        conn: sqlite3.Connection,
        deadline: float | None,
    ) -> None:
        busy_timeout_ms = self._remaining_shutdown_busy_timeout_ms(deadline)
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")

    def stop(self, *, deadline: float | None = None) -> None:
        """Close admission, interrupt bound runs, then drain until the deadline."""
        with self._active_cond:
            self._accepting = False
            first_stop = not self._stopping
            self._stopping = True
            active_ticket_ids = tuple(sorted(self._active_ticket_ids)) if first_stop else ()

        matched_session_turns: list[_MatchedEmployeeSessionTurnSnapshot] = []
        for ticket_id in active_ticket_ids:
            try:
                with closing(
                    connect(
                        self._db_path,
                        self._remaining_shutdown_busy_timeout_ms(deadline),
                    )
                ) as conn:
                    self._apply_remaining_shutdown_busy_timeout(conn, deadline)
                    conn.execute("BEGIN")
                    try:
                        ticket = tickets_data.read_ticket(conn, ticket_id)
                        employee_session_id = ticket.employee_session_id
                        if not employee_session_id:
                            continue
                        active_turn = chat_data.read_active_turn(conn, ticket_id)
                        if (
                            active_turn is None
                            or active_turn.origin != "worker"
                            or active_turn.mode != "worker_step"
                        ):
                            continue
                        turn_session_id = chat_data.read_running_turn_session_key(
                            conn,
                            active_turn.id,
                            entity_id=ticket_id,
                        )
                        if turn_session_id == employee_session_id:
                            matched_session_turns.append(
                                _MatchedEmployeeSessionTurnSnapshot(
                                    employee_session_id=employee_session_id,
                                    ticket_id=ticket_id,
                                    turn_id=active_turn.id,
                                )
                            )
                    finally:
                        conn.rollback()
            except sqlite3.OperationalError:
                _log.exception(
                    "employee shutdown snapshot could not acquire SQLite (ticket=%s)",
                    ticket_id,
                )
        matched_session_turn_snapshot = tuple(matched_session_turns)

        for matched_session_turn in matched_session_turn_snapshot:
            try:
                self._gateway.interrupt(
                    matched_session_turn.employee_session_id,
                    matched_session_turn.ticket_id,
                    deadline=deadline,
                )
            except Exception:
                _log.exception(
                    "employee session interrupt failed during shutdown (ticket=%s)",
                    matched_session_turn.ticket_id,
                )

        with self._active_cond:
            if deadline is None:
                self._active_cond.wait_for(lambda: self._active == 0)
            else:
                self._active_cond.wait_for(
                    lambda: self._active == 0,
                    timeout=max(0.0, deadline - _monotonic()),
                )
            active_ticket_ids_after_drain = frozenset(self._active_ticket_ids)

        turns_to_settle = tuple(
            (matched_session_turn.ticket_id, matched_session_turn.turn_id)
            for matched_session_turn in matched_session_turn_snapshot
            if matched_session_turn.ticket_id in active_ticket_ids_after_drain
        )
        for ticket_id, turn_id in turns_to_settle:
            try:
                with closing(
                    connect(
                        self._db_path,
                        self._remaining_shutdown_busy_timeout_ms(deadline),
                    )
                ) as conn:
                    self._apply_remaining_shutdown_busy_timeout(conn, deadline)
                    chat_data.settle_chat_turn(
                        conn,
                        turn_id,
                        entity_id=ticket_id,
                        status="interrupted",
                        reply_text="",
                        output_role="assistant",
                        error=None,
                        now=self._clock.now_unix(),
                    )
            except sqlite3.OperationalError:
                _log.exception(
                    "employee shutdown settlement could not acquire SQLite (ticket=%s)",
                    ticket_id,
                )

    def _is_stopping(self) -> bool:
        with self._active_cond:
            return self._stopping

    def _gateway_available(self) -> bool:
        try:
            return self._gateway.status().available
        except Exception:  # an unavailable adapter must reject before Ticket mutation
            return False

    def _run_automatic_step_thread(self, ticket_id: str) -> None:
        settled = False
        try:
            settled = self._run(
                ticket_id,
                revision_guidance=None,
                restart_recovery=False,
            )
        finally:
            self._finish_active(ticket_id, settled)

    def _run_recovery_thread(self, ticket_id: str) -> None:
        settled = False
        try:
            settled = self._run(
                ticket_id,
                revision_guidance=None,
                restart_recovery=True,
            )
        finally:
            self._finish_active(ticket_id, settled)

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
                settled = self._run(
                    ticket_id,
                    revision_guidance=guidance,
                    restart_recovery=False,
                )
        finally:
            self._finish_active(ticket_id, settled)

    def _finish_active(self, ticket_id: str, settled: bool) -> None:
        with self._active_cond:
            self._active_ticket_ids.discard(ticket_id)
            self._active -= 1
            if settled:
                self._automatic_employee_step_eligibility_wake.wake()
            self._active_cond.notify_all()

    def _run(
        self,
        ticket_id: str,
        *,
        revision_guidance: str | None,
        restart_recovery: bool,
    ) -> bool:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            now = self._clock.now_unix()
            if restart_recovery:
                claimed = tickets_data.read_ticket(conn, ticket_id)
                if claimed.ticket_status is not TicketStatus.agent_running_step:
                    return False
                if claimed.employee_session_id is None:
                    error = "restart recovery has no existing Employee session"
                    stale_worker_turn = chat_data.read_active_turn(conn, ticket_id)
                    if (
                        stale_worker_turn is not None
                        and stale_worker_turn.origin == "worker"
                        and stale_worker_turn.mode == "worker_step"
                    ):
                        chat_data.settle_chat_turn(
                            conn,
                            stale_worker_turn.id,
                            entity_id=ticket_id,
                            status="errored",
                            reply_text="",
                            output_role="system"
                            if stale_worker_turn.output_role == "system"
                            else "assistant",
                            error=error,
                            now=now,
                        )
                    tickets_data.mark_run_errored_if_still_running_step(
                        conn,
                        ticket_id,
                        error=error,
                        now=now,
                    )
                    return True
                prompt = _RESTART_RECOVERY_MESSAGE
                show_prompt_in_chat = False
                require_existing_session = True
            elif revision_guidance is None:
                automatic_claim = tickets_data.claim_automatic_employee_step(
                    conn,
                    ticket_id,
                    planning_day_id_resolver=lambda: dates.resolve_day_id(
                        "today", self._clock.now(), self._boundary_hour
                    ),
                    eligibility_check=(
                        automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
                    ),
                    now=now,
                )
                if automatic_claim is None:
                    _log.info(
                        "employee runner skipped a no-longer-eligible Ticket (ticket=%s)",
                        ticket_id,
                    )
                    return False
                claimed = automatic_claim
                worker_type_definition = configured_worker_type_registry().require(
                    claimed.worker_type
                )
                prompt = _next_step_prompt(
                    claimed,
                    worker_type_definition=worker_type_definition,
                )
                show_prompt_in_chat = True
                require_existing_session = False
            else:
                claimed = tickets_data.read_ticket(conn, ticket_id)
                if claimed.ticket_status is not TicketStatus.agent_running_step:
                    return False
                if claimed.employee_session_id is None:
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

            current_employee_session_id = claimed.employee_session_id
            if restart_recovery:
                worker_turn = chat_data.roll_running_turn_for_recovery(
                    conn,
                    ticket_id,
                    origin="worker",
                    mode="worker_step",
                    visible_role="system",
                    visible_text=prompt,
                    output_role="assistant",
                    phase="thinking",
                    activity_label="Restarting Employee",
                    now=now,
                    expected_session_key=claimed.employee_session_id,
                )
                if worker_turn is None:
                    tickets_data.mark_run_errored_if_still_running_step(
                        conn,
                        ticket_id,
                        error=(
                            "restart recovery stale worker turn Employee session id "
                            "does not match the stored Employee session"
                        ),
                        now=now,
                    )
                    return True
            else:
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

            def persist_employee_session_id(candidate_employee_session_id: str) -> None:
                nonlocal current_employee_session_id
                event_now = self._clock.now_unix()
                updated = tickets_data.claim_running_step_employee_session_id(
                    conn,
                    ticket_id,
                    transition=EmployeeSessionIdTransition(
                        expected_employee_session_id=current_employee_session_id,
                        candidate_employee_session_id=candidate_employee_session_id,
                    ),
                    now=event_now,
                )
                if (
                    updated.ticket_status is not TicketStatus.agent_running_step
                    or updated.employee_session_id != candidate_employee_session_id
                ):
                    raise _WorkerSessionClaimLost
                current_employee_session_id = updated.employee_session_id
                chat_service.attach_worker_session_key(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    candidate_employee_session_id,
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

            def finish_running_step(candidate_employee_session_id: str | None) -> None:
                if candidate_employee_session_id is None:
                    tickets_data.finish_run_if_still_running_step(conn, ticket_id, now=now)
                else:
                    tickets_data.finish_run_if_still_running_step(
                        conn,
                        ticket_id,
                        employee_session_transition=EmployeeSessionIdTransition(
                            expected_employee_session_id=current_employee_session_id,
                            candidate_employee_session_id=candidate_employee_session_id,
                        ),
                        now=now,
                    )

            def mark_errored(error: str, candidate_employee_session_id: str | None) -> None:
                if candidate_employee_session_id is None:
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
                        employee_session_transition=EmployeeSessionIdTransition(
                            expected_employee_session_id=current_employee_session_id,
                            candidate_employee_session_id=candidate_employee_session_id,
                        ),
                        now=now,
                    )

            try:
                if require_existing_session:
                    result = self._gateway.run_ticket_step(
                        claimed.employee_session_id,
                        ticket_id,
                        prompt,
                        observe_gateway_event,
                        on_session_key=persist_employee_session_id,
                        require_existing_session=True,
                    )
                else:
                    result = self._gateway.run_ticket_step(
                        claimed.employee_session_id,
                        ticket_id,
                        prompt,
                        observe_gateway_event,
                        on_session_key=persist_employee_session_id,
                    )
            except SharedGatewayBusy as exc:
                chat_service.fail_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    "session busy",
                    self._clock.now_unix(),
                )
                finish_running_step(exc.session_key or current_employee_session_id)
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
                finish_running_step(current_employee_session_id)
                return True
            except Exception as exc:  # never leave the Ticket at agent_running_step
                _log.exception("employee step crashed (ticket=%s)", ticket_id)
                error = f"employee step crashed: {exc}"
                if self._is_stopping():
                    chat_service.finish_worker_turn(
                        conn,
                        ticket_id,
                        worker_turn.id,
                        "",
                        "interrupted",
                        self._clock.now_unix(),
                    )
                    return False
                chat_service.fail_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    error,
                    self._clock.now_unix(),
                )
                mark_errored(error, current_employee_session_id)
                return True

            result_employee_session_id = result.session_key or current_employee_session_id
            if result.status == "complete":
                settled_worker_turn = chat_data.finish_turn(
                    conn,
                    worker_turn.id,
                    entity_id=ticket_id,
                    reply_text=result.text,
                    output_role="assistant",
                    status="complete",
                    now=self._clock.now_unix(),
                )
                if settled_worker_turn is None or settled_worker_turn.status != "complete":
                    if self._is_stopping():
                        return False
                    mark_errored(
                        (settled_worker_turn.error if settled_worker_turn is not None else None)
                        or "run interrupted",
                        result_employee_session_id,
                    )
                    return True
                finish_running_step(result_employee_session_id)
            elif result.status == "interrupted":
                chat_service.finish_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    result.text,
                    "interrupted",
                    self._clock.now_unix(),
                )
                if self._is_stopping():
                    return False
                mark_errored("run interrupted", result_employee_session_id)
            else:
                error = result.error or "gateway run failed"
                if self._is_stopping():
                    chat_service.finish_worker_turn(
                        conn,
                        ticket_id,
                        worker_turn.id,
                        result.text,
                        "interrupted",
                        self._clock.now_unix(),
                    )
                    return False
                chat_service.fail_worker_turn(
                    conn,
                    ticket_id,
                    worker_turn.id,
                    error,
                    self._clock.now_unix(),
                )
                mark_errored(error, result_employee_session_id)
            return True
        finally:
            conn.close()
