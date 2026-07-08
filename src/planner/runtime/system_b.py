"""System B — the set-off / run primitive.

Each set-off starts one daemon thread. The thread writes the durable
``agent_running_step`` transition, sends one prompt through the shared worker
gateway, and then records completion/error through transition-specific data
functions. There is no queue, no app busy registry, and no no-progress scan.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass

from planner.core.clock import Clock
from planner.core.db import connect
from planner.minds.shared_gateway import SharedGateway, SharedGatewayBusy
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket, TicketStatus

_log = logging.getLogger(__name__)

# An optional execution-time readiness re-check (runtime.readiness.is_runnable): the poll
# read and the step thread are not atomic, so a human drop / scope-stop / park / block in the
# gap must not run a stale prompt. Kept as an injected guard so System B owns no readiness
# logic and W3a's bare-set_off tests (guard=None) are unchanged.
RunGuard = Callable[[sqlite3.Connection, Ticket], bool]


class _WorkerSessionClaimLost(Exception):
    """The ticket stopped being the active worker step before the prompt started."""


@dataclass
class _Item:
    ticket_id: str
    role: str
    prompt: str
    guard: RunGuard | None = None


class SystemB:
    """Set-off primitive for ticket agent steps."""

    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        gateway: SharedGateway,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._gateway = gateway
        self._busy_timeout_ms = busy_timeout_ms
        self._idle_cb: Callable[[str], None] | None = None
        self._active = 0
        self._active_cond = threading.Condition()

    # --- public API ---------------------------------------------------------

    def set_off(
        self, ticket_id: str, role: str, prompt: str, *, guard: RunGuard | None = None
    ) -> None:
        """Set off step N of a ticket on a daemon thread."""
        item = _Item(ticket_id, role, prompt, guard)
        with self._active_cond:
            self._active += 1
        thread = threading.Thread(
            target=self._run_item, args=(item,), name=f"system-b-{ticket_id}", daemon=True
        )
        thread.start()

    def set_idle_callback(self, cb: Callable[[str], None]) -> None:
        """Register a callback fired when a set-off settles."""
        self._idle_cb = cb

    def _on_idle(self, ticket_id: str) -> None:
        cb = self._idle_cb
        if cb is not None:
            cb(ticket_id)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until every set-off has fully settled (test helper)."""
        with self._active_cond:
            return self._active_cond.wait_for(lambda: self._active == 0, timeout)

    # --- the step thread ----------------------------------------------------

    def _run_item(self, item: _Item) -> None:
        try:
            self._run(item.ticket_id, item.prompt, item.guard)
        finally:
            with self._active_cond:
                self._active -= 1
                self._active_cond.notify_all()
            self._on_idle(item.ticket_id)

    def _run(self, ticket_id: str, prompt: str, guard: RunGuard | None) -> None:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            now = self._clock.now_unix()
            pre = tickets_data.start_run_if_runnable(
                conn, ticket_id, guard=guard, now=now
            )
            if pre is None:
                _log.info("system B skipped a no-longer-runnable ticket (ticket=%s)", ticket_id)
                return
            current_session_key = pre.chat_session_key

            def persist_session_key(session_key: str) -> None:
                nonlocal current_session_key
                updated = tickets_data.claim_running_step_chat_session_key(
                    conn, ticket_id, session_key=session_key, now=now
                )
                if (
                    updated.ticket_status is not TicketStatus.agent_running_step
                    or updated.chat_session_key != session_key
                ):
                    raise _WorkerSessionClaimLost
                current_session_key = updated.chat_session_key

            def finish_running_step(session_key: str | None) -> None:
                if session_key is None:
                    tickets_data.finish_run_if_still_running_step(conn, ticket_id, now=now)
                else:
                    tickets_data.finish_run_if_still_running_step(
                        conn, ticket_id, session_key=session_key, now=now
                    )

            def mark_errored(error: str, session_key: str | None) -> None:
                if session_key is None:
                    tickets_data.mark_run_errored_if_still_running_step(
                        conn, ticket_id, error=error, now=now
                    )
                else:
                    tickets_data.mark_run_errored_if_still_running_step(
                        conn, ticket_id, error=error, session_key=session_key, now=now
                    )

            try:
                result = self._gateway.run_ticket_step(
                    pre.chat_session_key, prompt, None, on_session_key=persist_session_key
                )
            except SharedGatewayBusy as exc:
                finish_running_step(exc.session_key or current_session_key)
                return
            except _WorkerSessionClaimLost:
                _log.info("system B skipped an unowned worker session (ticket=%s)", ticket_id)
                finish_running_step(current_session_key)
                return
            except Exception as exc:  # never leave the ticket at agent_running_step
                _log.exception("system B run crashed (ticket=%s)", ticket_id)
                mark_errored(f"system B run crashed: {exc}", current_session_key)
                return
            current_session_key = result.session_key or current_session_key
            if result.status == "complete":
                finish_running_step(current_session_key)
            elif result.status == "interrupted":
                mark_errored("run interrupted", current_session_key)
            else:
                mark_errored(result.error or "gateway run failed", current_session_key)
        finally:
            conn.close()
