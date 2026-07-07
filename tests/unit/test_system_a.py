"""System A (W3b): the readiness poll + fast path, and the pure readiness predicate.

Hermetic against minds/fake.py — no subprocess, no model calls; the gateway child is injected
through the spawn seam and a fresh FakeGateway is handed out per spawn. The "agent files a
proposal during its run" is simulated by driving the REAL tickets_data.file_proposal writer on
prompt.submit, so auto-accept/park below/at ceiling flows through the production resolution
engine. Split into: (1) direct is_runnable predicate tests (no threads); (2) poll_once +
loop/poke/on_idle integration tests through a fake System B."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from planner.core import links as core_links
from planner.core.clock import RealClock, TestClock
from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import ChildProcess
from planner.runtime import readiness
from planner.runtime.system_a import SystemA
from planner.runtime.system_b import SystemB
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, FieldName, Ticket, TicketState, TicketStatus

HOME = "/tmp/planner-home"
HERMES_PY = "/x/hermes-agent/venv/bin/python"
LIVE_SID = "live-sid"
STORED_KEY = "stored-key-1"
ROLE = "planning-worker"

# System A is scoped to TODAY's day (owner ruling). Pin the clock so "today" is deterministic:
# planning_date(2026-07-06 12:00, boundary 5) = 2026-07-06 -> day_2026-07-06.
BOUNDARY_HOUR = 5
FIXED_NOW = datetime(2026, 7, 6, 12, 0, 0).astimezone()
TODAY_DAY_ID = "day_2026-07-06"
OTHER_DAY_ID = "day_2026-07-05"


# --- DB helpers ---------------------------------------------------------------


def _db(tmp_path: Path) -> str:
    db_path = tmp_path / "planning-test.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    return str(db_path)


def _new_ticket(
    db: str, *, ceiling: TicketState | None = None, at_cap: AtCap = AtCap.propose
) -> str:
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn, title="T", actor="human", now=0, title_max_chars=200
        )
        if ceiling is not None or at_cap is not AtCap.propose:
            tickets_data.change_grant(
                conn,
                ticket.id,
                ceiling=ceiling if ceiling is not None else TicketState.needs_success,
                at_cap=at_cap,
                actor="human",
                now=0,
            )
        return ticket.id
    finally:
        conn.close()


def _read(db: str, tid: str) -> Ticket:
    conn = connect(db)
    try:
        return tickets_data.read_ticket(conn, tid)
    finally:
        conn.close()


def _set_status(db: str, tid: str, status: TicketStatus, *, worker: str | None = None) -> None:
    conn = connect(db)
    try:
        tickets_data.set_run_status(conn, tid, status=status, worker=worker, now=0)
    finally:
        conn.close()


def _file_proposal(db: str, tid: str, field: str, body: str) -> None:
    conn = connect(db)
    try:
        tickets_data.file_proposal(
            conn, tid, field=FieldName(field), body=body, actor="agent", now=0
        )
    finally:
        conn.close()


def _grant(db: str, tid: str, ceiling: TicketState, at_cap: AtCap) -> None:
    conn = connect(db)
    try:
        tickets_data.change_grant(conn, tid, ceiling=ceiling, at_cap=at_cap, actor="human", now=0)
    finally:
        conn.close()


def _drop(db: str, tid: str) -> None:
    conn = connect(db)
    try:
        tickets_data.drop_ticket(conn, tid, actor="human", now=0)
    finally:
        conn.close()


def _jump_state(db: str, tid: str, state: TicketState) -> None:
    conn = connect(db)
    try:
        tickets_data.set_state(conn, tid, new_state=state, actor="human", now=0)
    finally:
        conn.close()


def _add_block(db: str, blocker_id: str, target_id: str) -> None:
    conn = connect(db)
    try:
        core_links.add_link(conn, blocker_id, target_id, LinkKind.blocks, 0)
    finally:
        conn.close()


def _add_to_day(db: str, tid: str, day_id: str = TODAY_DAY_ID) -> None:
    """Assign a ticket to a day (materializes the day). Default = today (the poll's scope)."""
    conn = connect(db)
    try:
        days_data.add_day_ticket(conn, day_id, tid, 0)
    finally:
        conn.close()


# --- fake-gateway scripting (mirrors test_system_b) ---------------------------


def _create_reply(sid: str = LIVE_SID, key: str = STORED_KEY) -> Reply:
    return Reply(result={"session_id": sid, "stored_session_id": key})


def _resume_reply(sid: str = LIVE_SID, key: str = STORED_KEY) -> Reply:
    return Reply(result={"session_id": sid, "resumed": key})


def _complete_ev(sid: str = LIVE_SID, status: str = "complete") -> dict[str, Any]:
    return ev("message.complete", sid, {"text": "ok", "usage": {}, "status": status})


def _submit_reply(*events_after: dict[str, Any]) -> Reply:
    return Reply(result={"status": "streaming"}, events_after=tuple(events_after))


def _create_script(*after: dict[str, Any]) -> dict[str, list[Reply]]:
    return {"session.create": [_create_reply()], "prompt.submit": [_submit_reply(*after)]}


def _resume_script(key: str = STORED_KEY, *after: dict[str, Any]) -> dict[str, list[Reply]]:
    return {"session.resume": [_resume_reply(key=key)], "prompt.submit": [_submit_reply(*after)]}


class _ProposingFake(FakeGateway):
    """Files a real proposal (production writer) the moment it receives prompt.submit."""

    def __init__(
        self, script: dict[str, list[Reply]], *, on_submit: Callable[[], None] | None = None
    ) -> None:
        super().__init__(script)
        self._on_submit = on_submit

    def send(self, line: str) -> None:
        frame = json.loads(line)
        if frame.get("method") == "prompt.submit" and self._on_submit is not None:
            self._on_submit()
        super().send(line)


class _Spawner:
    """Hands out a fresh FakeGateway per spawn (a child is single-use)."""

    def __init__(self, children: list[FakeGateway]) -> None:
        self._children = children
        self._i = 0
        self._lock = threading.Lock()

    def spawn(self, argv: list[str], env: dict[str, str]) -> ChildProcess:
        with self._lock:
            child = self._children[self._i]
            self._i += 1
        return child.spawn(argv, env)


def _system_b(db: str, spawner: _Spawner) -> SystemB:
    return SystemB(db, RealClock(), home=HOME, hermes_python=HERMES_PY, spawn=spawner.spawn)


def _system_a(db: str, sb: SystemB) -> SystemA:
    # Pinned clock so the day-scoped candidate query resolves "today" = TODAY_DAY_ID.
    return SystemA(db, TestClock(FIXED_NOW), sb, role=ROLE, boundary_hour=BOUNDARY_HOUR)


def _wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --- is_runnable predicate (no threads) ---------------------------------------


def test_is_runnable_fresh_empty_default_grant(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)  # needs_success, empty, ceiling=needs_success, at_cap=propose
    conn = connect(db)
    try:
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid)) is True
    finally:
        conn.close()


def test_is_runnable_at_ceiling_stop_is_false(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_success, at_cap=AtCap.stop)
    conn = connect(db)
    try:
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid)) is False
    finally:
        conn.close()


def test_is_runnable_at_ceiling_propose_is_true(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_success, at_cap=AtCap.propose)
    conn = connect(db)
    try:
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid)) is True
    finally:
        conn.close()


def test_is_runnable_parked_proposal_is_false(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _file_proposal(db, tid, "success", "b")  # parks at the default ceiling
    conn = connect(db)
    try:
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid)) is False
    finally:
        conn.close()


def test_is_runnable_needs_review_is_false(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _jump_state(db, tid, TicketState.needs_review)  # no gating field -> human approves
    conn = connect(db)
    try:
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid)) is False
    finally:
        conn.close()


def test_is_runnable_terminal_is_false(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _drop(db, tid)  # dropped is terminal
    conn = connect(db)
    try:
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid)) is False
    finally:
        conn.close()


def test_is_runnable_blocked_is_false(tmp_path: Path) -> None:
    db = _db(tmp_path)
    blocker = _new_ticket(db)          # open ticket -> blocks
    target = _new_ticket(db)
    _add_block(db, blocker, target)
    conn = connect(db)
    try:
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, target)) is False
    finally:
        conn.close()


def test_is_runnable_below_ceiling_after_auto_accept_is_true(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_plan)
    _file_proposal(db, tid, "success", "b")  # auto-accepts (below ceiling) -> needs_approach
    conn = connect(db)
    try:
        ticket = tickets_data.read_ticket(conn, tid)
        assert ticket.state is TicketState.needs_approach
        assert readiness.is_runnable(conn, ticket) is True  # next step (approach) is ready
    finally:
        conn.close()


# --- System A poll_once + fast path (integration through a fake System B) -----


def test_poll_sets_off_a_ready_ticket(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _add_to_day(db, tid)  # on today -> in scope
    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "b"),
    )
    sb = _system_b(db, _Spawner([fake]))
    sa = _system_a(db, sb)

    assert sa.poll_once() == [tid]
    assert sb.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.status == TicketStatus.awaiting_approval  # ran the step, parked at ceiling
    assert "session.create" in fake.sent_methods()          # kickoff = step 0 through create


def test_poll_excludes_every_non_runnable_ticket(tmp_path: Path) -> None:
    # All of these are ON today (so the day scope passes) but excluded by status or is_runnable.
    db = _db(tmp_path)
    # status-excluded (candidate query): agent_working, errored
    t_working = _new_ticket(db)
    _set_status(db, t_working, TicketStatus.agent_working, worker="w")
    t_errored = _new_ticket(db)
    _set_status(db, t_errored, TicketStatus.errored)
    # predicate-excluded: dropped, at-ceiling+stop, parked proposal, needs_review, blocked
    t_dropped = _new_ticket(db)
    _drop(db, t_dropped)
    t_stop = _new_ticket(db, ceiling=TicketState.needs_success, at_cap=AtCap.stop)
    t_parked = _new_ticket(db)
    _file_proposal(db, t_parked, "success", "b")
    t_review = _new_ticket(db)
    _jump_state(db, t_review, TicketState.needs_review)
    blocker = _new_ticket(db, at_cap=AtCap.stop)  # open (blocks) but itself not runnable
    t_blocked = _new_ticket(db)
    _add_block(db, blocker, t_blocked)
    for tid in (t_working, t_errored, t_dropped, t_stop, t_parked, t_review, blocker, t_blocked):
        _add_to_day(db, tid)

    sb = _system_b(db, _Spawner([]))  # must never be used
    sa = _system_a(db, sb)
    assert sa.poll_once() == []       # nothing ready -> nothing set off


def test_fast_path_poke_sets_off_before_the_timer(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid_box: list[str] = []
    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid_box[0], "success", "b"),
    )
    sb = _system_b(db, _Spawner([fake]))
    sa = _system_a(db, sb)
    sa.start(60)  # long interval: only a poke (not the timer) can drive it inside the budget
    try:
        time.sleep(0.3)               # let the first (empty) poll run and enter the wait
        tid = _new_ticket(db)
        _add_to_day(db, tid)          # on today -> in scope
        tid_box.append(tid)
        sa.poke()
        # the poke drove the set_off well under 60s (wait on the effect, not wait_idle, which
        # would race ahead of the loop thread's async poll+submit).
        assert _wait_until(lambda: _read(db, tid).status == TicketStatus.awaiting_approval, 10.0)
    finally:
        sa.stop()


def test_on_idle_drives_the_auto_advance_chain(tmp_path: Path) -> None:
    # ceiling=needs_approach: step 0 (success) auto-accepts and the finished-step on_idle poke
    # drives step 1 (approach) automatically, which parks at the ceiling and stops the chain.
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_approach)
    _add_to_day(db, tid)  # on today -> in scope
    fake0 = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "s"),
    )
    fake1 = _ProposingFake(
        _resume_script(STORED_KEY, _complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "approach", "a"),
    )
    sb = _system_b(db, _Spawner([fake0, fake1]))
    sa = _system_a(db, sb)
    sb.set_idle_callback(sa.poke)  # loops.py wires this in production
    sa.start(30)  # long interval: the on_idle poke, not the timer, advances the chain
    try:
        assert _wait_until(lambda: _read(db, tid).fields.approach.proposal is not None, 10.0)
    finally:
        sa.stop()

    ticket = _read(db, tid)
    assert ticket.state is TicketState.needs_approach       # step 0 auto-accepted + advanced
    assert ticket.fields.success.value == "s"                # step 0 value settled
    assert ticket.fields.approach.proposal is not None        # step 1 parked at the ceiling
    assert ticket.status == TicketStatus.awaiting_approval
    assert "session.create" in fake0.sent_methods()           # step 0 = create
    assert "session.resume" in fake1.sent_methods()           # step 1 = resume


# --- today-scoping (owner ruling: only today's tickets are auto-started) ------


def test_poll_is_scoped_to_today(tmp_path: Path) -> None:
    # The same ready ticket: a no-op while it is backlog (on no day), set off once on today.
    db = _db(tmp_path)
    tid = _new_ticket(db)  # fresh + runnable, but on no day
    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "b"),
    )
    sb = _system_b(db, _Spawner([fake]))
    sa = _system_a(db, sb)

    assert sa.poll_once() == []          # backlog -> out of scope, never spawned
    _add_to_day(db, tid)                 # now on today's day
    assert sa.poll_once() == [tid]       # in scope + runnable -> set off
    assert sb.wait_idle(10.0)
    assert _read(db, tid).status == TicketStatus.awaiting_approval


def test_poll_excludes_ticket_on_another_day(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)                 # fresh + runnable
    _add_to_day(db, tid, OTHER_DAY_ID)    # but on yesterday, not today
    sb = _system_b(db, _Spawner([]))      # must never be used
    sa = _system_a(db, sb)
    assert sa.poll_once() == []           # other-day ticket is out of scope


def test_poll_sets_off_today_ticket_after_approval_advance(tmp_path: Path) -> None:
    # The owner's primary flow: a ticket on today whose prior step advanced (state moved on,
    # gating field empty, still below ceiling) is set off for its NEXT step on the next poll —
    # exactly what the approve fast-path poke triggers (re-derive readiness -> set_off).
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_plan)
    _add_to_day(db, tid)
    _file_proposal(db, tid, "success", "s")               # auto-accepts below ceiling
    _set_status(db, tid, TicketStatus.awaiting_approval)  # as System B's end-write leaves it
    assert _read(db, tid).state is TicketState.needs_approach

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "approach", "a"),
    )
    sb = _system_b(db, _Spawner([fake]))
    sa = _system_a(db, sb)
    assert sa.poll_once() == [tid]                          # in scope + runnable -> next step
    assert sb.wait_idle(10.0)
    assert _read(db, tid).status == TicketStatus.awaiting_approval
