"""System A — the readiness poll + fast path. Decides which tickets are READY to be worked
and drives ``SystemB.set_off`` for each. It never touches the model and NEVER writes
``tickets.status`` (System B is the sole writer — notes.md Principles).

Readiness = a candidate-only query on the run status (``empty`` / ``awaiting_approval``,
non-terminal) refined by the pure ``runtime.readiness.is_runnable`` predicate AND the ticket
not already in-flight (``system_b.has_inflight``). The SAME ``is_runnable`` is handed to
``set_off`` as its execution-time guard, so a ticket that stops being runnable between the
poll and the run is skipped — one predicate gates both the poll and the run.

Fast path: an approval / unblock (from the API) or a finished step (the MindQueue idle
callback) pokes the poll immediately; the ``tick_seconds`` timer is the backstop. One poll
thread; a machine-wide lock (acquired by ``loops.py``) keeps a single poller per machine."""

from __future__ import annotations

import logging
import threading

from planner.core.clock import Clock
from planner.core.db import connect
from planner.days.logic import dates
from planner.runtime import readiness
from planner.runtime.system_b import SystemB
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket
from planner.tickets.logic import machine

_log = logging.getLogger(__name__)

# The candidate set: tickets ON TODAY'S day (owner ruling — backlog / other-day tickets are not
# auto-started) whose run status MIGHT need the next step set off, non-terminal. The day_tickets
# join scopes it; the status IN (...) excludes agent_working (running) and errored (stopped +
# surfaced; no auto-retry); state NOT IN excludes the two terminals. A candidate-only result set,
# not a per-tick full-table unpack.
_CANDIDATE_SQL = (
    "SELECT t.id FROM tickets t "
    "JOIN day_tickets dt ON dt.ticket_id = t.id "
    "WHERE dt.day_id = ? "
    "AND t.status IN ('empty','awaiting_approval') AND t.state NOT IN ('done','dropped')"
)


def _next_step_prompt(ticket: Ticket) -> str:
    """The next-step prompt handed to the mind. Minimal on purpose — the "how to work a
    ticket" intelligence lives in the worker role skill (loaded at kickoff), not here. Never
    carries approval machinery (notes.md Principles: approval is invisible to the model)."""
    gating = machine.gating_field(ticket.state)
    field = gating.value if gating is not None else "the next step"
    return (
        f"Work ticket {ticket.id} — {ticket.title}. It is in state '{ticket.state.value}'; "
        f"take the next step and propose the '{field}' field for approval."
    )


class SystemA:
    """Readiness poll + fast path. Drives System B; never writes state."""

    def __init__(
        self,
        db_path: str,
        clock: Clock,
        system_b: SystemB,
        *,
        role: str,
        boundary_hour: int,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._system_b = system_b
        self._role = role
        self._boundary_hour = boundary_hour  # resolves "today" for the day-scoped candidate query
        self._busy_timeout_ms = busy_timeout_ms
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- fast path ----------------------------------------------------------

    def poke(self, _key: str | None = None) -> None:
        """Wake the poll loop now (fast path). ``_key`` is accepted and ignored so this can
        be registered directly as the MindQueue idle callback (called with the ticket_id) and
        also called arg-less from the API poke path."""
        self._wake.set()

    # --- one readiness pass (also the unit-test seam) -----------------------

    def poll_once(self) -> list[str]:
        """One readiness pass: set off every ready ticket; return the ids set off. Ready =
        candidate query -> is_runnable -> not already in-flight. Reads only; the set_off
        (and its status write, via System B) happens after the read connection is closed."""
        today_id = dates.resolve_day_id("today", self._clock.now(), self._boundary_hour)
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            rows = conn.execute(_CANDIDATE_SQL, (today_id,)).fetchall()
            candidate_ids = [str(row["id"]) for row in rows]
            ready: list[Ticket] = []
            for ticket_id in candidate_ids:
                ticket = tickets_data.read_ticket(conn, ticket_id)
                if readiness.is_runnable(conn, ticket) and not self._system_b.has_inflight(
                    ticket_id
                ):
                    ready.append(ticket)
        finally:
            conn.close()
        for ticket in ready:
            self._system_b.set_off(
                ticket.id,
                self._role,
                _next_step_prompt(ticket),
                guard=readiness.is_runnable,
            )
        return [ticket.id for ticket in ready]

    # --- loop lifecycle -----------------------------------------------------

    def start(self, interval: int) -> None:
        """Spawn the daemon poll thread (production). ``interval`` is the timer backstop."""
        if self._thread is not None:
            raise RuntimeError("System A already started")
        self._thread = threading.Thread(
            target=self._run_loop, args=(interval,), name="system-a", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the loop to exit and join it (no new set-offs after this returns). In-flight
        MindQueue runs are daemon threads, abandoned on process exit (as in W1/W3a)."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10.0)
            self._thread = None

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # a bad pass never kills the loop
                _log.exception("system A poll failed")
            # Wait for a poke or the timer, then clear: a poke landing between poll_once and
            # wait sets the event, so wait returns at once and the next pass catches the work
            # — no lost wake.
            self._wake.wait(interval)
            self._wake.clear()
