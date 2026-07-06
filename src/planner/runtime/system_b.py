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
from dataclasses import dataclass
from pathlib import Path

from planner.core.clock import Clock
from planner.core.db import connect
from planner.minds.gateway import SpawnFn, spawn_popen
from planner.minds.queue import MindQueue
from planner.minds.runner import run_step
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TicketStatus
from planner.tickets.logic import fields_codec, machine

_log = logging.getLogger(__name__)


@dataclass
class _Item:
    ticket_id: str
    role: str
    prompt: str


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
        self._queue: MindQueue[_Item] = MindQueue(self._run_item)

    # --- public API ---------------------------------------------------------

    def set_off(self, ticket_id: str, role: str, prompt: str) -> None:
        """Set off step N of a ticket. Serialized per ticket through the MindQueue;
        the run resolves the mind's current session_key at execution time."""
        self._queue.submit(ticket_id, _Item(ticket_id, role, prompt))

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until every set-off has fully settled (test helper)."""
        return self._queue.wait_idle(timeout)

    # --- the serialized run -------------------------------------------------

    def _run_item(self, _key: str, item: _Item) -> None:
        # _key is the queue key (== ticket_id); resolve the CURRENT session_key from the
        # DB here, at execution time, so a rotated key from the prior run is picked up.
        session_key = self._read_session_key(item.ticket_id)
        self._run(item.ticket_id, session_key, item.role, item.prompt)

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

    def _run(self, ticket_id: str, key: str | None, role: str, prompt: str) -> None:
        """One step: agent_working (start write) -> run_step -> proposal-present
        invariant -> awaiting_approval/errored (end write). Both writes go through the
        single-door tickets_data.set_run_status; System B is the only caller. The end
        write ALWAYS fires (even if the run or a read raises) so a ticket is never left
        stuck at agent_working."""
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            now = self._clock.now_unix()
            # (1) start write: agent_working + worker, single atomic UPDATE.
            tickets_data.set_run_status(
                conn, ticket_id, status=TicketStatus.agent_working, worker=role, now=now
            )
            # (2..4) run + map. Any failure between the writes maps to errored so the end
            # write below is unconditional.
            end_status = TicketStatus.errored
            end_error: str | None = None
            session_key_out = key
            try:
                pre = tickets_data.read_ticket(conn, ticket_id)  # the step the code asked for
                pre_state = pre.state
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
