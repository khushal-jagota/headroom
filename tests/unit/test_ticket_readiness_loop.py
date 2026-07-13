"""TicketReadinessLoop discovery and the pure readiness predicate.

Hermetic against minds/fake.py — no subprocess, no model calls; the gateway child is injected
through the spawn seam and a fresh FakeGateway is handed out per spawn. The "agent files a
proposal during its run" is simulated by driving the REAL tickets_data.file_proposal writer on
prompt.submit, so auto-accept/park below/at ceiling flows through the production resolution
engine. Split into: (1) direct is_runnable predicate tests (no threads); (2) poll_once +
loop/wake/settlement-doorbell integration tests through a fake employee gateway."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from planner.core import links as core_links
from planner.core.adapters.registry import build_adapters
from planner.core.clock import TestClock
from planner.core.config import load_config
from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.shared_gateway import SharedGateway
from planner.runtime import readiness
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.readiness_doorbell import LoopReadinessDoorbell, NoOpReadinessDoorbell
from planner.runtime.ticket_readiness_loop import TicketReadinessLoop
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, FieldName, Ticket, TicketState, TicketStatus
from planner.tickets.logic import fields_codec

HOME = "/tmp/planner-home"
HERMES_PY = sys.executable
LIVE_SID = "live-sid"
STORED_KEY = "stored-key-1"
ROLE = "planning-worker"

# Discovery is scoped to TODAY. Pin the clock so "today" is deterministic:
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
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field=FieldName.kickoff,
            actor="human",
            now=0,
            next_ceiling="none",
            at_cap=AtCap.propose,
        )
        if ceiling is not None or at_cap is not AtCap.propose:
            tickets_data.change_scope(
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


def _new_kickoff_ticket(db: str) -> str:
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn, title="T", actor="human", now=0, title_max_chars=200
        )
        return ticket.id
    finally:
        conn.close()


def _set_status(db: str, tid: str, status: TicketStatus) -> None:
    conn = connect(db)
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = ?, updated_at = ? WHERE id = ?",
            (status.value, 0, tid),
        )
    finally:
        conn.close()


def _set_key(db: str, tid: str, key: str) -> None:
    conn = connect(db)
    try:
        conn.execute("UPDATE tickets SET chat_session_key = ? WHERE id = ?", (key, tid))
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


def _scope(db: str, tid: str, ceiling: TicketState, at_cap: AtCap) -> None:
    conn = connect(db)
    try:
        tickets_data.change_scope(conn, tid, ceiling=ceiling, at_cap=at_cap, actor="human", now=0)
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


# --- fake-gateway scripting ---------------------------------------------------


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
        self,
        script: dict[str, list[Reply]],
        *,
        on_submit: Callable[[], None] | list[Callable[[], None]] | None = None,
    ) -> None:
        super().__init__(script)
        if on_submit is None:
            self._on_submit: list[Callable[[], None]] = []
        elif isinstance(on_submit, list):
            self._on_submit = list(on_submit)
        else:
            self._on_submit = [on_submit]

    def send(self, line: str) -> None:
        frame = json.loads(line)
        if frame.get("method") == "prompt.submit" and self._on_submit:
            callback = self._on_submit.pop(0)
            callback()
        super().send(line)


def _runner(db: str, fake: FakeGateway) -> EmployeeStepRunner:
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=fake.spawn,
        base_env={},
    )
    return EmployeeStepRunner(
        db,
        TestClock(FIXED_NOW),
        gateway=gateway,
        readiness_doorbell=NoOpReadinessDoorbell(),
        boundary_hour=BOUNDARY_HOUR,
    )


def _loop(db: str, runner: EmployeeStepRunner) -> TicketReadinessLoop:
    # Pinned clock so the day-scoped candidate query resolves "today" = TODAY_DAY_ID.
    return TicketReadinessLoop(
        db,
        TestClock(FIXED_NOW),
        runner,
        boundary_hour=BOUNDARY_HOUR,
    )


def _wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --- is_runnable predicate (no threads) ---------------------------------------


def test_is_runnable_fresh_empty_default_scope(tmp_path: Path) -> None:
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


def test_is_runnable_kickoff_uses_generic_parked_proposal_predicate(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    tid = _new_kickoff_ticket(db)
    conn = connect(db)
    try:
        ticket = tickets_data.read_ticket(conn, tid)
        assert ticket.state == TicketState.needs_kickoff
        assert fields_codec.get_slot(ticket.fields, "kickoff").proposal is not None
        assert readiness.is_runnable(conn, ticket) is False
        assert tickets_data.read_ticket(conn, tid).ticket_status is TicketStatus.awaiting_approval
    finally:
        conn.close()


def test_is_runnable_new_worker_novel_stage_does_not_raise(tmp_path: Path) -> None:
    # Regression (Codex P1): is_runnable resolves the ticket's OWN definition, so a
    # new_worker ticket at the NOVEL needs_stages stage is classified against new_worker's
    # stages and returns a bool WITHOUT raising "state outside the linear order" (which it
    # would if the machine predicates defaulted to coding). A raise here aborts the whole
    # readiness poll (ticket_readiness_loop.poll_once iterates candidates with no per-row
    # guard), so this predicate must never raise for a foreign-type row.
    db = _db(tmp_path)
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn, title="Design a worker", actor="human", now=0, title_max_chars=200,
            ticket_type="new_worker",
        )
        # Accept kickoff, expanding the ceiling so the ticket lands at needs_stages with
        # room to propose (runnable). Novel stage, non-coding type.
        ticket = tickets_data.accept_proposal(
            conn, ticket.id, field=FieldName.kickoff, actor="human", now=0,
            next_ceiling="needs_stages", at_cap=AtCap.propose,
        )
        assert ticket.state == "needs_stages"
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, ticket.id)) is True
    finally:
        conn.close()


def test_is_runnable_new_worker_at_ceiling_stop_is_false(tmp_path: Path) -> None:
    # The scope predicate also resolves new_worker's definition: at the needs_stages
    # ceiling with at_cap=stop the ticket is NOT runnable (mirrors the coding case), again
    # without raising on the novel stage.
    db = _db(tmp_path)
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn, title="Design a worker", actor="human", now=0, title_max_chars=200,
            ticket_type="new_worker",
        )
        ticket = tickets_data.accept_proposal(
            conn, ticket.id, field=FieldName.kickoff, actor="human", now=0,
            next_ceiling="none", at_cap=AtCap.stop,
        )
        assert ticket.state == "needs_stages"
        assert ticket.ceiling == "needs_stages"
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, ticket.id)) is False
    finally:
        conn.close()


def test_is_runnable_needs_closeout_follows_ordinary_gating(tmp_path: Path) -> None:
    # closeout is a field-gated state like every other: no special-cased "no gating
    # field, human must approve" behavior remains in the five-field model.
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_closeout, at_cap=AtCap.propose)
    _jump_state(db, tid, TicketState.needs_closeout)
    conn = connect(db)
    try:
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid)) is True
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
    tid = _new_ticket(db, ceiling=TicketState.needs_approach)
    _file_proposal(db, tid, "success", "b")  # auto-accepts (below ceiling) -> needs_approach
    conn = connect(db)
    try:
        ticket = tickets_data.read_ticket(conn, tid)
        assert ticket.state == TicketState.needs_approach
        assert readiness.is_runnable(conn, ticket) is True  # next step (approach) is ready
    finally:
        conn.close()


# --- readiness poll + fast path -----------------------------------------------


def test_poll_sets_off_a_ready_ticket(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _add_to_day(db, tid)  # on today -> in scope
    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "b"),
    )
    runner = _runner(db, fake)
    loop = _loop(db, runner)

    assert loop.poll_once() == [tid]
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.awaiting_approval
    assert "session.create" in fake.sent_methods()          # kickoff = step 0 through create


def test_poll_passes_only_ticket_id_to_runner_interface(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _add_to_day(db, tid)

    class RecordingRunner:
        def __init__(self) -> None:
            self.ticket_ids: list[str] = []

        def run_ready_step(self, ticket_id: str) -> None:
            self.ticket_ids.append(ticket_id)

    runner = RecordingRunner()
    loop = TicketReadinessLoop(
        db,
        TestClock(FIXED_NOW),
        runner,  # type: ignore[arg-type]
        boundary_hour=BOUNDARY_HOUR,
    )

    assert loop.poll_once() == [tid]
    assert runner.ticket_ids == [tid]


def test_poll_excludes_every_non_runnable_ticket(tmp_path: Path) -> None:
    # All of these are ON today (so the day scope passes) but excluded by status or is_runnable.
    db = _db(tmp_path)
    # ticket_status-excluded (candidate query)
    t_working = _new_ticket(db)
    _set_status(db, t_working, TicketStatus.agent_running_step)
    t_approval = _new_ticket(db)
    _set_status(db, t_approval, TicketStatus.awaiting_approval)
    t_takeover = _new_ticket(db)
    _set_status(db, t_takeover, TicketStatus.user_takeover)
    t_errored = _new_ticket(db)
    _set_status(db, t_errored, TicketStatus.errored)
    # predicate-excluded: dropped, at-ceiling+stop, parked proposal, blocked
    t_dropped = _new_ticket(db)
    _drop(db, t_dropped)
    t_stop = _new_ticket(db, ceiling=TicketState.needs_success, at_cap=AtCap.stop)
    t_parked = _new_ticket(db)
    _file_proposal(db, t_parked, "success", "b")
    blocker = _new_ticket(db, at_cap=AtCap.stop)  # open (blocks) but itself not runnable
    t_blocked = _new_ticket(db)
    _add_block(db, blocker, t_blocked)
    for tid in (
        t_working,
        t_approval,
        t_takeover,
        t_errored,
        t_dropped,
        t_stop,
        t_parked,
        blocker,
        t_blocked,
    ):
        _add_to_day(db, tid)

    runner = _runner(db, FakeGateway({}))  # must never be used
    loop = _loop(db, runner)
    assert loop.poll_once() == []       # nothing ready -> nothing started


def test_fast_path_wake_sets_off_before_the_timer(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid_box: list[str] = []
    first_empty_scan = threading.Event()
    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid_box[0], "success", "b"),
    )
    runner = _runner(db, fake)

    class ObservableLoop(TicketReadinessLoop):
        def poll_once(self) -> list[str]:
            result = super().poll_once()
            first_empty_scan.set()
            return result

    loop = ObservableLoop(
        db,
        TestClock(FIXED_NOW),
        runner,
        boundary_hour=BOUNDARY_HOUR,
    )
    loop.start(60)  # only a wake, not the timer, can drive it inside the budget
    try:
        assert first_empty_scan.wait(3.0)
        tid = _new_ticket(db)
        _add_to_day(db, tid)          # on today -> in scope
        tid_box.append(tid)
        loop.wake()
        # the wake drove the employee step well under 60s (wait on the effect, not wait_idle, which
        # would race ahead of the loop thread's async poll+submit).
        assert _wait_until(
            lambda: _read(db, tid).ticket_status == TicketStatus.awaiting_approval,
            10.0,
        )
    finally:
        loop.stop()


def test_settlement_doorbell_drives_the_auto_advance_chain(tmp_path: Path) -> None:
    # ceiling=needs_approach: step 0 (success) auto-accepts and its settlement doorbell
    # drives step 1 (approach) automatically, which parks at the ceiling and stops the chain.
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_approach)
    _add_to_day(db, tid)  # on today -> in scope
    fake = _ProposingFake(
        {
            "session.create": [_create_reply()],
            "session.resume": [_resume_reply(key=STORED_KEY)],
            "prompt.submit": [_submit_reply(_complete_ev()), _submit_reply(_complete_ev())],
        },
        on_submit=[
            lambda: _file_proposal(db, tid, "success", "s"),
            lambda: _file_proposal(db, tid, "approach", "a"),
        ],
    )
    loop_box: list[TicketReadinessLoop] = []
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=fake.spawn,
        base_env={},
    )
    runner = EmployeeStepRunner(
        db,
        TestClock(FIXED_NOW),
        gateway=gateway,
        readiness_doorbell=LoopReadinessDoorbell(lambda: loop_box[0].wake()),
        boundary_hour=BOUNDARY_HOUR,
    )
    loop = _loop(db, runner)
    loop_box.append(loop)
    loop.start(30)  # the settlement ring, not the timer, advances the chain
    try:
        assert _wait_until(
            lambda: fields_codec.get_slot(_read(db, tid).fields, "approach").proposal is not None,
            10.0,
        )
    finally:
        loop.stop()

    ticket = _read(db, tid)
    assert ticket.state == TicketState.needs_approach       # step 0 auto-accepted + advanced
    assert fields_codec.get_slot(ticket.fields, "success").value == "s"  # step 0 value settled
    assert (
        fields_codec.get_slot(ticket.fields, "approach").proposal is not None
    )  # step 1 parked at the ceiling
    assert ticket.ticket_status == TicketStatus.awaiting_approval
    assert fake.sent_methods().count("session.create") == 1
    assert fake.sent_methods().count("session.resume") == 1


def test_fastapi_day_action_rings_real_loop_without_waiting_for_long_timer(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    first_scan = threading.Event()
    dispatched = threading.Event()
    dispatched_ids: list[str] = []

    class RecordingRunner:
        def run_ready_step(self, ticket_id: str) -> None:
            dispatched_ids.append(ticket_id)
            dispatched.set()

    class ObservableLoop(TicketReadinessLoop):
        def poll_once(self) -> list[str]:
            result = super().poll_once()
            first_scan.set()
            return result

    runner = RecordingRunner()
    loop = ObservableLoop(
        db,
        TestClock(FIXED_NOW),
        runner,  # type: ignore[arg-type]
        boundary_hour=BOUNDARY_HOUR,
    )
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": db,
            "PLAN_BOUNDARY_HOUR": str(BOUNDARY_HOUR),
        },
    )

    def conn_factory():
        return connect(db)

    app = create_app(config, TestClock(FIXED_NOW), build_adapters(config), conn_factory)
    app.state.readiness_doorbell = LoopReadinessDoorbell(loop.wake)
    loop.start(3600)
    try:
        assert first_scan.wait(3.0)
        tid = _new_ticket(db)
        with TestClient(app) as client:
            placed = client.post("/api/day/today/tickets", json={"ticket_id": tid})
        assert placed.status_code == 200, placed.text
        assert dispatched.wait(3.0)
        assert dispatched_ids == [tid]
    finally:
        loop.stop()


# --- today-scoping (owner ruling: only today's tickets are auto-started) ------


def test_poll_is_scoped_to_today(tmp_path: Path) -> None:
    # The same ready ticket: a no-op while it is backlog (on no day), set off once on today.
    db = _db(tmp_path)
    tid = _new_ticket(db)  # fresh + runnable, but on no day
    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "b"),
    )
    runner = _runner(db, fake)
    loop = _loop(db, runner)

    assert loop.poll_once() == []          # backlog -> out of scope, never spawned
    _add_to_day(db, tid)                 # now on today's day
    assert loop.poll_once() == [tid]       # in scope + runnable -> set off
    assert runner.wait_idle(10.0)
    assert _read(db, tid).ticket_status == TicketStatus.awaiting_approval


def test_poll_excludes_ticket_on_another_day(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)                 # fresh + runnable
    _add_to_day(db, tid, OTHER_DAY_ID)    # but on yesterday, not today
    runner = _runner(db, FakeGateway({}))      # must never be used
    loop = _loop(db, runner)
    assert loop.poll_once() == []           # other-day Ticket is out of scope


def test_poll_sets_off_today_ticket_after_approval_advance(tmp_path: Path) -> None:
    # The owner's primary flow: a ticket on today whose prior step advanced (state moved on,
    # gating field empty, still below ceiling) is set off for its NEXT step on the next poll —
    # exactly what the approval doorbell triggers (re-derive readiness -> run a step).
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_approach)
    _add_to_day(db, tid)
    _file_proposal(db, tid, "success", "s")               # auto-accepts below ceiling
    _set_key(db, tid, STORED_KEY)
    assert _read(db, tid).state == TicketState.needs_approach

    fake = _ProposingFake(
        _resume_script(STORED_KEY, _complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "approach", "a"),
    )
    runner = _runner(db, fake)
    loop = _loop(db, runner)
    assert loop.poll_once() == [tid]                          # ready -> next step
    assert runner.wait_idle(10.0)
    assert _read(db, tid).ticket_status == TicketStatus.awaiting_approval
