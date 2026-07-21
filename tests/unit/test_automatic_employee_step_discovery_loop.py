"""Read-only Automatic Employee-step discovery and its timer/wake behavior.

Hermetic against minds/fake.py: the full proposal flow uses the real Ticket writers and an
injected fake step gateway, while interface-focused tests record the one complete eligibility
call and the ticket ids submitted after the discovery connection closes.
"""

from __future__ import annotations

import ast
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from planner.core import links as core_links
from planner.core.clock import TestClock
from planner.core.config import load_config
from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.runtime import automatic_employee_step_eligibility
from planner.runtime.automatic_employee_step_discovery_loop import (
    AutomaticEmployeeStepDiscoveryLoop,
)
from planner.runtime.automatic_employee_step_eligibility_wake import (
    LoopAutomaticEmployeeStepEligibilityWake,
    NoOpAutomaticEmployeeStepEligibilityWake,
)
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.step_gateway import EmployeeStepRunResult
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, Ticket, TicketStatus
from planner.tickets.logic import fields_codec

STORED_KEY = "stored-key-1"

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
    db: str,
    *,
    ceiling: str | None = None,
    at_cap: AtCap = AtCap.propose,
    worker_type: str = "coding",
    project_id: str | None = None,
    sprint_item_id: str | None = None,
) -> str:
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type=worker_type,
            title="T",
            actor="human",
            now=0,
            title_max_chars=200,
            project_id=project_id,
            sprint_item_id=sprint_item_id,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=0,
            next_ceiling="none",
            at_cap=AtCap.propose,
        )
        if ceiling is not None or at_cap is not AtCap.propose:
            tickets_data.change_scope(
                conn,
                ticket.id,
                ceiling=ceiling if ceiling is not None else "needs_success",
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


def _new_item(db: str, project_id: str) -> str:
    conn = connect(db)
    try:
        return sprints_data.create_item(
            conn,
            title="Item",
            project_id=project_id,
            clock=TestClock(FIXED_NOW),
        ).id
    finally:
        conn.close()


def _new_kickoff_ticket(db: str) -> str:
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn, worker_type="coding", title="T", actor="human", now=0, title_max_chars=200
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


def _set_updated_at(db: str, tid: str, updated_at: int) -> None:
    conn = connect(db)
    try:
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (updated_at, tid))
    finally:
        conn.close()


def _set_key(db: str, tid: str, key: str) -> None:
    conn = connect(db)
    try:
        conn.execute("UPDATE tickets SET employee_session_id = ? WHERE id = ?", (key, tid))
    finally:
        conn.close()


def _file_proposal(db: str, tid: str, field: str, body: str) -> None:
    conn = connect(db)
    try:
        tickets_data.file_proposal(conn, tid, field=field, body=body, actor="agent", now=0)
    finally:
        conn.close()


def _scope(db: str, tid: str, ceiling: str, at_cap: AtCap) -> None:
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


def _jump_state(db: str, tid: str, state: str) -> None:
    conn = connect(db)
    try:
        tickets_data.set_stage(conn, tid, new_stage=state, actor="human", now=0)
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


# --- ACP step-gateway fixture -------------------------------------------------


def _create_script(*_after: object) -> object:
    return object()


def _resume_script(_key: str = STORED_KEY, *_after: object) -> object:
    return object()


def _complete_ev(*_args: object, **_kwargs: object) -> object:
    return object()


class _ProposingFake:
    """Files a real proposal when the runner admits one ACP prompt."""

    def __init__(
        self,
        _script: object,
        *,
        on_submit: Callable[[], None] | list[Callable[[], None]] | None = None,
    ) -> None:
        if on_submit is None:
            self._on_submit: list[Callable[[], None]] = []
        elif isinstance(on_submit, list):
            self._on_submit = list(on_submit)
        else:
            self._on_submit = [on_submit]

        self._methods: list[str] = []

    def run_ticket_step(
        self,
        employee_session_id: str | None,
        entity_id: str,
        prompt_text: str,
        on_employee_session_id: Callable[[str], None] | None = None,
        *,
        require_existing_session: bool = False,
    ) -> EmployeeStepRunResult:
        del entity_id, prompt_text, require_existing_session
        session_id = employee_session_id or STORED_KEY
        self._methods.extend(
            ["session.resume" if employee_session_id else "session.create", "prompt.submit"]
        )
        if on_employee_session_id is not None:
            on_employee_session_id(session_id)
        if self._on_submit:
            callback = self._on_submit.pop(0)
            callback()
        return EmployeeStepRunResult("complete", session_id, None)

    def interrupt(
        self,
        employee_session_id: str,
        entity_id: str,
        *,
        deadline: float | None = None,
    ) -> None:
        del employee_session_id, entity_id, deadline

    def status(self) -> object:
        return type("Status", (), {"available": True})()

    def sent_methods(self) -> list[str]:
        return list(self._methods)


class _UnusedFake(_ProposingFake):
    def __init__(self) -> None:
        super().__init__(object())


def _runner(db: str, fake: _ProposingFake) -> EmployeeStepRunner:
    return EmployeeStepRunner(
        db,
        TestClock(FIXED_NOW),
        gateway=fake,
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )


def _loop(db: str, runner: EmployeeStepRunner) -> AutomaticEmployeeStepDiscoveryLoop:
    # Pinned clock so the day-scoped candidate query resolves "today" = TODAY_DAY_ID.
    return AutomaticEmployeeStepDiscoveryLoop(
        db,
        TestClock(FIXED_NOW),
        runner,
        boundary_hour=BOUNDARY_HOUR,
    )


class _RecordingRunner:
    def __init__(self) -> None:
        self.ticket_ids: list[str] = []

    def try_run_automatic_step(self, ticket_id: str) -> None:
        self.ticket_ids.append(ticket_id)


def _recording_loop(db: str) -> tuple[AutomaticEmployeeStepDiscoveryLoop, _RecordingRunner]:
    runner = _RecordingRunner()
    return (
        AutomaticEmployeeStepDiscoveryLoop(
            db,
            TestClock(FIXED_NOW),
            runner,  # type: ignore[arg-type]
            boundary_hour=BOUNDARY_HOUR,
        ),
        runner,
    )


def _wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --- discovery and complete-decision delegation ----------------------------------


def test_candidate_sql_is_membership_only_and_every_member_reaches_one_complete_function(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(tmp_path)
    selected = _new_ticket(db)
    terminal = _new_ticket(db)
    non_empty = _new_ticket(db)
    for ticket_id in (selected, terminal, non_empty):
        _add_to_day(db, ticket_id)
    _jump_state(db, terminal, "done")
    _set_status(db, non_empty, TicketStatus.user_takeover)

    calls: list[tuple[str, str, str]] = []

    def record_complete_decision(
        conn: Any,
        ticket: Ticket,
        *,
        planning_day_id: str,
        worker_type_definition: Any,
    ) -> bool:
        assert conn.in_transaction is False
        calls.append((ticket.id, planning_day_id, worker_type_definition.worker_type))
        return ticket.id == selected

    monkeypatch.setattr(
        automatic_employee_step_eligibility,
        "is_eligible_for_automatic_employee_step",
        record_complete_decision,
    )

    class RecordingRunner:
        def __init__(self) -> None:
            self.ticket_ids: list[str] = []

        def try_run_automatic_step(self, ticket_id: str) -> None:
            # The discovery read connection has closed before submission.
            probe = connect(db)
            probe.execute("BEGIN IMMEDIATE")
            probe.rollback()
            probe.close()
            self.ticket_ids.append(ticket_id)

    runner = RecordingRunner()
    loop = AutomaticEmployeeStepDiscoveryLoop(
        db,
        TestClock(FIXED_NOW),
        runner,  # type: ignore[arg-type]
        boundary_hour=BOUNDARY_HOUR,
    )

    assert loop.poll_once() == [selected]
    assert {ticket_id for ticket_id, _, _ in calls} == {
        selected,
        terminal,
        non_empty,
    }
    assert all(day_id == TODAY_DAY_ID for _, day_id, _ in calls)
    assert all(worker_type == "coding" for _, _, worker_type in calls)
    assert runner.ticket_ids == [selected]


def test_only_today_membership_is_presented_with_the_explicit_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(tmp_path)
    no_day = _new_ticket(db)
    other_day = _new_ticket(db)
    today = _new_ticket(db)
    _add_to_day(db, other_day, OTHER_DAY_ID)
    _add_to_day(db, today)
    calls: list[tuple[str, str]] = []

    def record(
        _conn: Any,
        ticket: Ticket,
        *,
        planning_day_id: str,
        worker_type_definition: Any,
    ) -> bool:
        assert worker_type_definition.worker_type == ticket.worker_type
        calls.append((ticket.id, planning_day_id))
        return False

    monkeypatch.setattr(
        automatic_employee_step_eligibility,
        "is_eligible_for_automatic_employee_step",
        record,
    )
    loop = _loop(db, _runner(db, _UnusedFake()))

    assert loop.poll_once() == []
    assert calls == [(today, TODAY_DAY_ID)]
    assert no_day not in {ticket_id for ticket_id, _ in calls}
    assert other_day not in {ticket_id for ticket_id, _ in calls}


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
    assert "session.create" in fake.sent_methods()  # kickoff = step 0 through create


def test_poll_passes_only_ticket_id_to_runner_interface(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _add_to_day(db, tid)

    loop, runner = _recording_loop(db)

    assert loop.poll_once() == [tid]
    assert runner.ticket_ids == [tid]


@pytest.mark.parametrize(
    "status",
    [
        TicketStatus.agent_running_step,
        TicketStatus.awaiting_approval,
        TicketStatus.user_takeover,
        TicketStatus.paired_work,
        TicketStatus.errored,
    ],
)
def test_closeout_waiter_is_not_submitted_while_its_projectless_worker_lane_is_occupied(
    tmp_path: Path,
    status: TicketStatus,
) -> None:
    db = _db(tmp_path)
    active = _new_ticket(db)
    waiting = _new_ticket(db)
    _jump_state(db, active, "needs_closeout")
    _jump_state(db, waiting, "needs_closeout")
    _set_status(db, active, status)
    _add_to_day(db, waiting)

    loop, runner = _recording_loop(db)

    assert loop.poll_once() == []
    assert runner.ticket_ids == []


def test_closeout_discovery_submits_only_the_oldest_waiter_in_one_free_lane(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    oldest = _new_ticket(db)
    newest = _new_ticket(db)
    for ticket_id in (oldest, newest):
        _jump_state(db, ticket_id, "needs_closeout")
        _add_to_day(db, ticket_id)
    _set_updated_at(db, oldest, 10)
    _set_updated_at(db, newest, 20)

    loop, runner = _recording_loop(db)

    assert loop.poll_once() == [oldest]
    assert runner.ticket_ids == [oldest]


def test_closeout_lane_uses_the_parent_sprint_item_project(tmp_path: Path) -> None:
    db = _db(tmp_path)
    item_id = _new_item(db, "project_vylo")
    active = _new_ticket(db, project_id="project_vylo")
    waiting = _new_ticket(db, sprint_item_id=item_id)
    _jump_state(db, active, "needs_closeout")
    _jump_state(db, waiting, "needs_closeout")
    _set_status(db, active, TicketStatus.awaiting_approval)
    _add_to_day(db, waiting)

    loop, runner = _recording_loop(db)

    assert loop.poll_once() == []
    assert runner.ticket_ids == []


def test_different_project_or_worker_type_closeout_lanes_are_independent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_ids = {
        _new_ticket(db, project_id="project_vylo"),
        _new_ticket(db, project_id="project_learning"),
        _new_ticket(db, worker_type="exploration", project_id="project_vylo"),
    }
    for ticket_id in ticket_ids:
        _jump_state(db, ticket_id, "needs_closeout")
        _add_to_day(db, ticket_id)

    loop, runner = _recording_loop(db)

    assert set(loop.poll_once()) == ticket_ids
    assert set(runner.ticket_ids) == ticket_ids


def test_empty_stopped_closeout_neither_runs_nor_occupies_the_lane(tmp_path: Path) -> None:
    db = _db(tmp_path)
    stopped = _new_ticket(db)
    continuing = _new_ticket(db)
    for ticket_id in (stopped, continuing):
        _jump_state(db, ticket_id, "needs_closeout")
        _add_to_day(db, ticket_id)
    _scope(db, stopped, "needs_closeout", AtCap.stop)

    loop, runner = _recording_loop(db)

    assert loop.poll_once() == [continuing]
    assert runner.ticket_ids == [continuing]


def test_closeout_claim_rechecks_lane_occupancy_inside_the_write_transaction(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    first = _new_ticket(db)
    second = _new_ticket(db)
    for ticket_id in (first, second):
        _jump_state(db, ticket_id, "needs_closeout")
        _add_to_day(db, ticket_id)

    conn = connect(db)
    try:
        def claim(ticket_id: str) -> Ticket | None:
            return tickets_data.claim_automatic_employee_step(
                conn,
                ticket_id,
                planning_day_id_resolver=lambda: TODAY_DAY_ID,
                eligibility_check=(
                    automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
                ),
                now=1,
            )

        assert claim(first) is not None
        assert claim(second) is None
    finally:
        conn.close()


def test_accepting_closeout_frees_the_lane_for_the_next_waiter(tmp_path: Path) -> None:
    db = _db(tmp_path)
    active = _new_ticket(db)
    waiting = _new_ticket(db)
    for ticket_id in (active, waiting):
        _jump_state(db, ticket_id, "needs_closeout")
    _file_proposal(db, active, "closeout", "closed")
    _add_to_day(db, waiting)

    loop, runner = _recording_loop(db)
    assert loop.poll_once() == []

    conn = connect(db)
    try:
        accepted = tickets_data.accept_proposal(
            conn,
            active,
            field="closeout",
            actor="human",
            now=1,
            next_ceiling="none",
            at_cap=AtCap.propose,
        )
    finally:
        conn.close()

    assert accepted.stage == "done"
    assert loop.poll_once() == [waiting]
    assert runner.ticket_ids == [waiting]


def test_non_closeout_tickets_do_not_share_closeout_lane_serialization(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_ids = {_new_ticket(db), _new_ticket(db)}
    for ticket_id in ticket_ids:
        _add_to_day(db, ticket_id)

    loop, runner = _recording_loop(db)

    assert set(loop.poll_once()) == ticket_ids
    assert set(runner.ticket_ids) == ticket_ids


def test_closeout_resumes_the_existing_ticket_employee_session(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    _jump_state(db, ticket_id, "needs_closeout")
    _set_key(db, ticket_id, STORED_KEY)
    _add_to_day(db, ticket_id)
    fake = _ProposingFake(
        _resume_script(STORED_KEY, _complete_ev()),
        on_submit=lambda: _file_proposal(db, ticket_id, "closeout", "closed"),
    )
    runner = _runner(db, fake)

    assert _loop(db, runner).poll_once() == [ticket_id]
    assert runner.wait_idle(10.0)
    assert _read(db, ticket_id).ticket_status is TicketStatus.awaiting_approval
    assert "session.resume" in fake.sent_methods()
    assert "session.create" not in fake.sent_methods()


def test_poll_excludes_every_ineligible_ticket(tmp_path: Path) -> None:
    # All are on today, but each is excluded by one part of the complete decision.
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
    t_stop = _new_ticket(db, ceiling="needs_success", at_cap=AtCap.stop)
    t_parked = _new_ticket(db)
    _file_proposal(db, t_parked, "success", "b")
    blocker = _new_ticket(db, at_cap=AtCap.stop)  # open (blocks) but itself not eligible
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

    runner = _runner(db, _UnusedFake())  # must never be used
    loop = _loop(db, runner)
    assert loop.poll_once() == []  # nothing ready -> nothing started


def test_fast_path_wake_sets_off_before_the_timer(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid_box: list[str] = []
    first_empty_scan = threading.Event()
    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid_box[0], "success", "b"),
    )
    runner = _runner(db, fake)

    class ObservableLoop(AutomaticEmployeeStepDiscoveryLoop):
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
        _add_to_day(db, tid)  # on today -> in scope
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


def test_settlement_eligibility_wake_drives_the_auto_advance_chain(tmp_path: Path) -> None:
    # ceiling=needs_approach: step 0 (success) auto-accepts and its settlement eligibility_wake
    # drives step 1 (approach) automatically, which parks at the ceiling and stops the chain.
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling="needs_approach")
    _add_to_day(db, tid)  # on today -> in scope
    fake = _ProposingFake(
        object(),
        on_submit=[
            lambda: _file_proposal(db, tid, "success", "s"),
            lambda: _file_proposal(db, tid, "approach", "a"),
        ],
    )
    loop_box: list[AutomaticEmployeeStepDiscoveryLoop] = []
    runner = EmployeeStepRunner(
        db,
        TestClock(FIXED_NOW),
        gateway=fake,
        automatic_employee_step_eligibility_wake=LoopAutomaticEmployeeStepEligibilityWake(
            lambda: loop_box[0].wake()
        ),
        boundary_hour=BOUNDARY_HOUR,
    )
    loop = _loop(db, runner)
    loop_box.append(loop)
    loop.start(30)  # the settlement wake, not the timer, advances the chain
    try:
        assert _wait_until(
            lambda: fields_codec.get_slot(_read(db, tid).fields, "approach").proposal is not None,
            10.0,
        )
    finally:
        loop.stop()

    ticket = _read(db, tid)
    assert ticket.stage == "needs_approach"  # step 0 auto-accepted + advanced
    assert fields_codec.get_slot(ticket.fields, "success").value == "s"  # step 0 value settled
    assert (
        fields_codec.get_slot(ticket.fields, "approach").proposal is not None
    )  # step 1 parked at the ceiling
    assert ticket.ticket_status == TicketStatus.awaiting_approval
    assert fake.sent_methods().count("session.create") == 1
    assert fake.sent_methods().count("session.resume") == 1


def test_fastapi_day_action_wakes_real_loop_without_waiting_for_long_timer(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    first_scan = threading.Event()
    dispatched = threading.Event()
    dispatched_ids: list[str] = []

    class RecordingRunner:
        def try_run_automatic_step(self, ticket_id: str) -> None:
            dispatched_ids.append(ticket_id)
            dispatched.set()

    class ObservableLoop(AutomaticEmployeeStepDiscoveryLoop):
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
            "PLAN_DB_PATH": db,
            "PLAN_BOUNDARY_HOUR": str(BOUNDARY_HOUR),
        },
    )

    def conn_factory():
        return connect(db)

    app = create_app(config, TestClock(FIXED_NOW), conn_factory)
    app.state.automatic_employee_step_eligibility_wake = LoopAutomaticEmployeeStepEligibilityWake(
        loop.wake
    )
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
    tid = _new_ticket(db)  # fresh + eligible, but on no day
    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "b"),
    )
    runner = _runner(db, fake)
    loop = _loop(db, runner)

    assert loop.poll_once() == []  # backlog -> out of scope, never spawned
    _add_to_day(db, tid)  # now on today's day
    assert loop.poll_once() == [tid]  # in scope + eligible -> set off
    assert runner.wait_idle(10.0)
    assert _read(db, tid).ticket_status == TicketStatus.awaiting_approval


def test_poll_excludes_ticket_on_another_day(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)  # fresh + eligible
    _add_to_day(db, tid, OTHER_DAY_ID)  # but on yesterday, not today
    runner = _runner(db, _UnusedFake())  # must never be used
    loop = _loop(db, runner)
    assert loop.poll_once() == []  # other-day Ticket is out of scope


def test_poll_sets_off_today_ticket_after_approval_advance(tmp_path: Path) -> None:
    # The owner's primary flow: a ticket on today whose prior step advanced (state moved on,
    # gating field empty, still below ceiling) is set off for its NEXT step on the next poll —
    # exactly what the approval eligibility_wake triggers (re-evaluate eligibility -> run a step).
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling="needs_approach")
    _add_to_day(db, tid)
    _file_proposal(db, tid, "success", "s")  # auto-accepts below ceiling
    _set_key(db, tid, STORED_KEY)
    assert _read(db, tid).stage == "needs_approach"

    fake = _ProposingFake(
        _resume_script(STORED_KEY, _complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "approach", "a"),
    )
    runner = _runner(db, fake)
    loop = _loop(db, runner)
    assert loop.poll_once() == [tid]  # ready -> next step
    assert runner.wait_idle(10.0)
    assert _read(db, tid).ticket_status == TicketStatus.awaiting_approval


def test_new_worker_paired_understanding_dispatches_one_opening_and_not_a_second(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="new_worker",
            title="Design a worker",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=0,
            next_ceiling="needs_stages",
            at_cap=AtCap.propose,
        )
        days_data.add_day_ticket(conn, TODAY_DAY_ID, ticket.id, 0)
    finally:
        conn.close()

    runner = _runner(db, _ProposingFake(_create_script(_complete_ev())))
    loop = _loop(db, runner)

    assert _read(db, ticket.id).stage == "needs_understanding"
    assert loop.poll_once() == [ticket.id]
    assert runner.wait_idle(10.0)
    stored = _read(db, ticket.id)
    assert stored.stage == "needs_understanding"
    assert fields_codec.get_slot(stored.fields, "understanding").proposal is None
    assert stored.ticket_status is TicketStatus.paired_work
    assert loop.poll_once() == []


def test_new_worker_discovery_runs_after_understanding_is_approved(tmp_path: Path) -> None:
    db = _db(tmp_path)
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="new_worker",
            title="Design a worker",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=0,
            next_ceiling="needs_stages",
            at_cap=AtCap.propose,
        )
        tickets_data.file_proposal(
            conn,
            ticket.id,
            field="understanding",
            body="bounded understanding",
            actor="agent",
            now=0,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="understanding",
            actor="human",
            now=0,
            next_ceiling="needs_stages",
            at_cap=AtCap.propose,
        )
        days_data.add_day_ticket(conn, TODAY_DAY_ID, ticket.id, 0)
    finally:
        conn.close()

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, ticket.id, "stages", "stage design"),
    )
    runner = _runner(db, fake)

    assert _loop(db, runner).poll_once() == [ticket.id]
    assert runner.wait_idle(10.0)
    stored = _read(db, ticket.id)
    assert stored.worker_type == "new_worker"
    assert stored.stage == "needs_stages"
    assert fields_codec.get_slot(stored.fields, "stages").proposal is not None
    assert stored.ticket_status is TicketStatus.awaiting_approval


def test_periodic_timer_is_the_backstop_when_no_wake_is_delivered(tmp_path: Path) -> None:
    db = _db(tmp_path)
    first_scan = threading.Event()
    submitted = threading.Event()
    submitted_ids: list[str] = []

    class RecordingRunner:
        def try_run_automatic_step(self, ticket_id: str) -> None:
            submitted_ids.append(ticket_id)
            submitted.set()

    class ObservableLoop(AutomaticEmployeeStepDiscoveryLoop):
        def poll_once(self) -> list[str]:
            result = super().poll_once()
            first_scan.set()
            return result

    loop = ObservableLoop(
        db,
        TestClock(FIXED_NOW),
        RecordingRunner(),  # type: ignore[arg-type]
        boundary_hour=BOUNDARY_HOUR,
    )
    loop.start(1)
    try:
        assert first_scan.wait(3.0)
        ticket_id = _new_ticket(db)
        # Direct data placement deliberately delivers no eligibility wake.
        _add_to_day(db, ticket_id)
        assert submitted.wait(3.0)
        assert submitted_ids == [ticket_id]
    finally:
        loop.stop()


def test_periodic_timer_observes_employee_step_settlement_without_a_wake(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    _add_to_day(db, ticket_id)
    conn = connect(db)
    try:
        run = SqliteEmployeeStepRepository().start(conn, ticket_id, now=1)
    finally:
        conn.close()

    two_blocked_scans = threading.Event()
    submitted = threading.Event()
    submitted_ids: list[str] = []
    scan_count = 0
    scan_lock = threading.Lock()

    class RecordingRunner:
        def try_run_automatic_step(self, submitted_ticket_id: str) -> None:
            submitted_ids.append(submitted_ticket_id)
            submitted.set()

    class ObservableLoop(AutomaticEmployeeStepDiscoveryLoop):
        def poll_once(self) -> list[str]:
            nonlocal scan_count
            result = super().poll_once()
            with scan_lock:
                scan_count += 1
                if scan_count >= 2:
                    two_blocked_scans.set()
            return result

    loop = ObservableLoop(
        db,
        TestClock(FIXED_NOW),
        RecordingRunner(),  # type: ignore[arg-type]
        boundary_hour=BOUNDARY_HOUR,
    )
    loop.start(0.05)  # type: ignore[arg-type]
    try:
        assert two_blocked_scans.wait(3.0)
        assert submitted_ids == []

        conn = connect(db)
        try:
            # This low-level settlement deliberately delivers no eligibility wake.
            SqliteEmployeeStepRepository().settle(
                conn,
                run.employee_step_id,
                ticket_id=ticket_id,
                status="complete",
                error=None,
                now=2,
            )
        finally:
            conn.close()

        assert submitted.wait(3.0)
        assert submitted_ids[0] == ticket_id
    finally:
        loop.stop()


def test_discovery_starts_once_and_stop_joins_the_thread(tmp_path: Path) -> None:
    db = _db(tmp_path)
    loop = _loop(db, _runner(db, _UnusedFake()))
    loop.start(60)
    thread = loop._thread
    assert thread is not None
    assert thread.name == "automatic-employee-step-discovery-loop"
    with pytest.raises(
        RuntimeError,
        match="automatic employee-step discovery loop already started",
    ):
        loop.start(60)
    loop.stop()
    assert not thread.is_alive()
    assert loop._thread is None


def test_poll_exception_is_logged_and_the_periodic_loop_survives(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _db(tmp_path)
    recovered = threading.Event()

    class FlakyLoop(AutomaticEmployeeStepDiscoveryLoop):
        def __init__(self) -> None:
            super().__init__(
                db,
                TestClock(FIXED_NOW),
                _runner(db, _UnusedFake()),
                boundary_hour=BOUNDARY_HOUR,
            )
            self.calls = 0

        def poll_once(self) -> list[str]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient poll failure")
            recovered.set()
            return []

    loop = FlakyLoop()
    with caplog.at_level("ERROR"):
        loop.start(0.01)  # type: ignore[arg-type]
        try:
            assert recovered.wait(3.0)
        finally:
            loop.stop()
    assert "automatic employee-step discovery poll failed" in caplog.text
    assert "transient poll failure" in caplog.text


def test_discovery_source_orders_membership_sql_without_copying_eligibility() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "src/planner/runtime/automatic_employee_step_discovery_loop.py"
    source = path.read_text()
    normalized = " ".join(source.split()).lower()
    expected_sql = (
        "select t.id from tickets t join day_tickets dt on dt.ticket_id = t.id "
        "where dt.day_id = ? \" \"order by t.updated_at, t.id"
    )
    assert expected_sql in normalized
    candidate = normalized.split("_candidate_sql", 1)[1].split(")", 1)[0]
    for forbidden in (
        "ticket_status",
        "stage",
        "proposal",
        "ceiling",
        "at_cap",
        "block",
        "field",
        "chat",
        "worker_type",
    ):
        assert forbidden not in candidate
    assert "is_eligible_for_automatic_employee_step" in source
    assert "try_run_automatic_step" in source
    for forbidden_rule in (
        "TicketStatus",
        "is_terminal(",
        "gating_field(",
        "has_pending_parked_proposal",
        "at_or_beyond_ceiling",
        "is_blocked",
    ):
        assert forbidden_rule not in source


def test_live_employee_runtime_has_no_deleted_names_or_old_wake_operation() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        *sorted((root / "src/planner/runtime").glob("*.py")),
        root / "src/planner/tickets/actions.py",
        root / "src/planner/tickets/api.py",
        root / "src/planner/days/actions.py",
        root / "src/planner/days/api.py",
        root / "src/planner/core/link_actions.py",
        root / "src/planner/core/loops.py",
        root / "src/planner/core/server.py",
    ]
    rejected = (
        "is_runnable",
        "TicketReadinessLoop",
        "ticket_readiness_loop",
        "run_ready_step",
        "start_run_if_runnable",
        "ReadinessDoorbell",
        "readiness_doorbell",
    )
    for path in paths:
        source = path.read_text()
        for name in rejected:
            assert name not in source, (path, name)
        assert ".ring(" not in source, path


def test_deleted_runtime_names_are_not_live_python_identifiers_or_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        *sorted((root / "src/planner").rglob("*.py")),
        *sorted((root / "tests").rglob("*.py")),
    ]
    rejected = (
        "is_runnable",
        "TicketReadinessLoop",
        "ticket_readiness_loop",
        "run_ready_step",
        "start_run_if_runnable",
        "ReadinessDoorbell",
        "readiness_doorbell",
    )
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id)
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifiers.add(node.name)
            elif isinstance(node, ast.arg):
                identifiers.add(node.arg)
            elif isinstance(node, ast.Import):
                identifiers.update(alias.name for alias in node.names)
                identifiers.update(alias.asname for alias in node.names if alias.asname)
            elif isinstance(node, ast.ImportFrom):
                identifiers.add(node.module or "")
                identifiers.update(alias.name for alias in node.names)
                identifiers.update(alias.asname for alias in node.names if alias.asname)
        for old_name in rejected:
            assert all(old_name not in identifier for identifier in identifiers), (
                path,
                old_name,
            )


def test_live_runtime_docs_and_root_instructions_use_the_new_names() -> None:
    root = Path(__file__).resolve().parents[2]
    config_lines = (root / "config.yaml").read_text().splitlines()
    assert config_lines[8] == (
        "dispatch_enabled: true             "
        "# automatic Employee-step discovery/dispatch startup switch (§7.1)"
    )

    paths = (
        root / "AGENTS.md",
        root / "CLAUDE.md",
        root / "docs/employee-runtime.md",
        root / "docs/systems.md",
        root / "docs/systems.html",
        root / "docs/tickets-and-gates.md",
    )
    rejected = (
        "runtime/readiness.py",
        "runtime/ticket_readiness_loop.py",
        "runtime/readiness_doorbell.py",
        "TicketReadinessLoop",
        "ReadinessDoorbell",
        "run_ready_step",
        "start_run_if_runnable",
        "is_runnable",
        "commit -> ring",
    )
    combined = "\n".join(path.read_text() for path in paths)
    for old_name in rejected:
        assert old_name not in combined, old_name
    assert "Automatic Employee-step eligibility" in combined
    assert "AutomaticEmployeeStepDiscoveryLoop" in combined
    assert "AutomaticEmployeeStepEligibilityWake" in combined
    assert "automatic_employee_step_discovery_loop.py" in combined
    assert "commit -> wake" in combined


def test_allowed_wake_test_helpers_use_eligibility_wake_names() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = (
        root / "tests/typing/tt02b_field_seam_cases.py",
        root / "tests/unit/test_chief_external_work.py",
        root / "tests/unit/test_core_loops.py",
        root / "tests/unit/test_day_api.py",
        root / "tests/unit/test_employee_step_runner.py",
        root / "tests/unit/test_return_for_revision.py",
        root / "tests/unit/test_ticket_delete.py",
        root / "tests/unit/test_tickets_engine.py",
        root / "tests/unit/test_value_edit_api.py",
        root / "tests/unit/test_automatic_employee_step_discovery_loop.py",
        root / "tests/unit/test_automatic_employee_step_eligibility_wake.py",
        root / "tests/unit/test_automatic_employee_step_eligibility_actions.py",
    )
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        assert not {
            name
            for name in identifiers
            if "doorbell" in name.lower() or "ring_count" in name.lower() or name.lower() == "rings"
        }, path


def test_wake_contract_is_payload_free_and_has_no_delivery_infrastructure() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "src/planner/runtime/automatic_employee_step_eligibility_wake.py"
    source = path.read_text()
    for forbidden in (
        "ticket_id",
        "sqlite",
        "queue",
        "socket",
        "multiprocessing",
        "subprocess",
        "ipc",
    ):
        assert forbidden not in source.lower()
    tree = ast.parse(source, filename=str(path))
    wake_functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "wake"
    ]
    assert wake_functions
    assert all(
        [argument.arg for argument in function.args.args] == ["self"]
        and not function.args.vararg
        and not function.args.kwarg
        for function in wake_functions
    )
