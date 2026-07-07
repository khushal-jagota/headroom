"""System B — the set-off / run primitive. "Run step N of ticket X": assemble the
role env, drive W1's ``run_step`` (spawn a gateway child, create/resume the mind,
submit one prompt, observe the single run-end), run the proposal-present invariant,
and write the ticket's run-status. System B is the SOLE writer of ``tickets.status``
(→ notes.md Principles: "code owns all state stamps; agents propose, one door writes").

Serialization — keyed on ``ticket_id`` (the STABLE per-mind identity):
  One mind == one ticket, so every run of a ticket must be serialized (at most one
  in-flight) and must resume the mind's CURRENT durable ``session_key``. The durable
  key is NOT a stable identity — the gateway auto-compresses and ROTATES it mid-run
  (spike 01: resume then follows the rotation chain to the live tip). Keying the
  ``MindQueue`` on the rotating key value would split one mind across two keys and let
  two children resume it concurrently, defeating the gateway's per-process busy-guard
  (the very hazard the queue exists to prevent). So the queue is keyed on ``ticket_id``
  (its "one in-flight per key" property maps exactly to "one in-flight run per mind"),
  and the current ``session_key`` is resolved from the DB at EXECUTION time inside the
  serialized run.

  This also subsumes W1's "System B must serialize kickoff itself": step-0 (no key
  yet) and step-N of the same ticket share the ``ticket_id`` key, so the queue
  serializes them; the run resolves ``None`` -> ``session.create`` (kickoff) and stores
  the created key, and the next run resolves it -> ``session.resume``. Two step-0 runs
  never both create.

Kickoff = step 0 through the same ``run_step`` call. ``errored`` stops and surfaces
(no auto-retry). System B is not wired into the running server in W3a — System A (the
readiness poll that calls ``set_off``) is W3b; this is exercised by unit tests against
``minds/fake.py``."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from planner.core.clock import Clock
from planner.core.db import connect
from planner.minds.gateway import SpawnFn, spawn_popen
from planner.minds.queue import MindQueue
from planner.minds.runner import run_step
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket, TicketStatus
from planner.tickets.logic import fields_codec, machine

_log = logging.getLogger(__name__)

# An optional execution-time readiness re-check (runtime.readiness.is_runnable): the poll
# read and the queued run are not atomic, so a human drop / grant-stop / park / block in the
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
    """Set-off primitive + the sole writer of ticket run-status."""

    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        home: str | Path,
        hermes_python: str | Path,
        spawn: SpawnFn = spawn_popen,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._home = home
        self._python = hermes_python
        self._spawn = spawn
        self._busy_timeout_ms = busy_timeout_ms
        self._idle_cb: Callable[[str], None] | None = None
        self._queue: MindQueue[_Item] = MindQueue(self._run_item, on_idle=self._on_idle)

    # --- public API ---------------------------------------------------------

    def set_off(
        self, ticket_id: str, role: str, prompt: str, *, guard: RunGuard | None = None
    ) -> None:
        """Set off step N of a ticket. Serialized per ticket through the MindQueue; the run
        resolves the mind's current session_key at execution time. ``guard`` (System A's
        ``is_runnable``) is re-checked at execution time before anything is written, so a
        ticket that stopped being runnable in the read→run gap is skipped, not run."""
        self._queue.submit(ticket_id, _Item(ticket_id, role, prompt, guard))

    def has_inflight(self, ticket_id: str) -> bool:
        """Whether a run is enqueued or in-flight for this ticket's mind. System A's
        readiness guard against re-setting-off a ticket whose ``agent_working`` start
        write hasn't landed yet (the enqueue happens before the queue thread writes it)."""
        return self._queue.is_active(ticket_id)

    def set_idle_callback(self, cb: Callable[[str], None]) -> None:
        """Register a callback fired when a ticket's queue drains — the mind is free
        again. W3b's System A registers its ``poke`` here so a finished step drives the
        next one immediately (fast path) rather than waiting a full poll tick."""
        self._idle_cb = cb

    def _on_idle(self, ticket_id: str) -> None:
        cb = self._idle_cb
        if cb is not None:
            cb(ticket_id)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until every set-off has fully settled (test helper)."""
        return self._queue.wait_idle(timeout)

    # --- the serialized run -------------------------------------------------

    def _run_item(self, _key: str, item: _Item) -> None:
        # _key is the queue key (== ticket_id); resolve the CURRENT session_key from the
        # DB here, at execution time, so a rotated key from the prior run is picked up.
        session_key = self._read_session_key(item.ticket_id)
        self._run(item.ticket_id, session_key, item.role, item.prompt, item.guard)

    def _read_session_key(self, ticket_id: str) -> str | None:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            row = conn.execute(
                "SELECT chat_session_key FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if row is None:
                return None
            value = row["chat_session_key"]
            return str(value) if value is not None else None
        finally:
            conn.close()

    def _run(
        self,
        ticket_id: str,
        key: str | None,
        role: str,
        prompt: str,
        guard: RunGuard | None,
    ) -> None:
        """One step: agent_working (start write) -> run_step -> proposal-present
        invariant -> awaiting_approval/errored (end write). Both writes go through the
        single-door tickets_data.set_run_status; System B is the only caller. The end
        write ALWAYS fires (even if the run or a read raises) so a ticket is never left
        stuck at agent_working."""
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            now = self._clock.now_unix()
            # (0+1) atomic guarded start: re-read + readiness re-check + the agent_working start
            # write happen in ONE immediate transaction, so a human drop / grant-stop / block /
            # park committed in the poll->run gap cannot slip a stale start-write + spawn past the
            # guard. Returns None (nothing written) => no longer runnable, so skip cleanly. Absent
            # guard (W3a bare set_off) => an unconditional start, exactly as before.
            pre = tickets_data.start_run_if_runnable(
                conn, ticket_id, worker=role, guard=guard, now=now
            )
            if pre is None:
                _log.info("system B skipped a no-longer-runnable ticket (ticket=%s)", ticket_id)
                return
            # (2..4) run + map. Any failure between here and the end write maps to errored so the
            # end write below is unconditional.
            end_status = TicketStatus.errored
            end_error: str | None = None
            session_key_out = key
            try:
                pre_state = pre.state  # the step the code asked for (re-read in the guarded start)
                pre_gating = machine.gating_field(pre_state)
                result = run_step(  # kickoff creates step 0; continuing resumes `key`
                    key, role, prompt, None, home=self._home, hermes_python=self._python,
                    spawn=self._spawn,
                )
                session_key_out = result.session_key
                end_error = result.error
                if result.status == "complete":
                    post = tickets_data.read_ticket(conn, ticket_id)
                    advanced = post.state != pre_state  # agent step only auto-accepts forward
                    parked = (
                        pre_gating is not None
                        and fields_codec.get_slot(post.fields, pre_gating).proposal is not None
                    )
                    if advanced or parked:
                        end_status = TicketStatus.awaiting_approval
                        end_error = None
                    else:
                        end_error = "agent left no proposal — you haven't done your job"
            except Exception as exc:  # never leave the ticket at agent_working
                _log.exception("system B run crashed (ticket=%s)", ticket_id)
                end_status = TicketStatus.errored
                end_error = f"system B run crashed: {exc}"

            # (5) end write: status + cleared worker + resolved durable key, one UPDATE.
            tickets_data.set_run_status(
                conn,
                ticket_id,
                status=end_status,
                worker=None,
                session_key=session_key_out,
                error=end_error if end_status == TicketStatus.errored else None,
                now=now,
            )
        finally:
            conn.close()
