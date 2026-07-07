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
from planner.tickets.contracts import Ticket

_log = logging.getLogger(__name__)

# An optional execution-time readiness re-check (runtime.readiness.is_runnable): the poll
# read and the step thread are not atomic, so a human drop / scope-stop / park / block in the
# gap must not run a stale prompt. Kept as an injected guard so System B owns no readiness
# logic and W3a's bare-set_off tests (guard=None) are unchanged.
RunGuard = Callable[[sqlite3.Connection, Ticket], bool]


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
            try:
                result = self._gateway.run_ticket_step(pre.chat_session_key, prompt, None)
            except SharedGatewayBusy as exc:
                tickets_data.finish_run_if_still_running_step(
                    conn, ticket_id, session_key=exc.session_key, now=now
                )
                return
            except Exception as exc:  # never leave the ticket at agent_running_step
                _log.exception("system B run crashed (ticket=%s)", ticket_id)
                tickets_data.mark_run_errored(
                    conn,
                    ticket_id,
                    error=f"system B run crashed: {exc}",
                    session_key=pre.chat_session_key,
                    now=now,
                )
                return
            if result.status == "complete":
                tickets_data.finish_run_if_still_running_step(
                    conn, ticket_id, session_key=result.session_key, now=now
                )
            elif result.status == "interrupted":
                tickets_data.mark_run_errored(
                    conn,
                    ticket_id,
                    error="run interrupted",
                    session_key=result.session_key,
                    now=now,
                )
            else:
                tickets_data.mark_run_errored(
                    conn,
                    ticket_id,
                    error=result.error or "gateway run failed",
                    session_key=result.session_key,
                    now=now,
                )
        finally:
            conn.close()
