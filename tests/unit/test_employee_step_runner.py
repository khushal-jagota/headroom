"""EmployeeStepRunner against a hermetic shared fake gateway."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from planner.chat import service as chat_service
from planner.core.clock import RealClock
from planner.core.contracts import EventKind
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import read_events_since
from planner.days import data as days_data
from planner.days.logic import dates
from planner.minds.contracts import OnEvent, RunResult
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.shared_gateway import SharedGateway
from planner.runtime import readiness
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.readiness_doorbell import NoOpReadinessDoorbell
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.contracts import AtCap, FieldName, TicketState, TicketStatus

HOME = "/tmp/planner-home"
HERMES_PY = sys.executable
LIVE_SID = "live-sid"
STORED_KEY = "stored-key-1"
ROLE = "planning-worker"
BOUNDARY_HOUR = 5


class _RecordingDoorbell:
    def __init__(self) -> None:
        self.calls = 0

    def ring(self) -> None:
        self.calls += 1


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


def _db(tmp_path: Path) -> str:
    db_path = tmp_path / "planning-test.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    return str(db_path)


def _new_ticket(db_path: str, *, ceiling: TicketState | None = None) -> str:
    conn = connect(db_path)
    try:
        ticket = tickets_data.create_ticket(
            conn, title="T", actor="human", now=0, title_max_chars=200
        )
        if ceiling is not None:
            tickets_data.change_scope(
                conn, ticket.id, ceiling=ceiling, at_cap=AtCap.propose, actor="human", now=0
            )
        today_id = dates.resolve_day_id("today", RealClock().now(), BOUNDARY_HOUR)
        days_data.add_day_ticket(conn, today_id, ticket.id, 0)
        return ticket.id
    finally:
        conn.close()


def _read(db_path: str, ticket_id: str) -> Any:
    conn = connect(db_path)
    try:
        return tickets_data.read_ticket(conn, ticket_id)
    finally:
        conn.close()


def _file_proposal(db_path: str, ticket_id: str, field: str, body: str) -> None:
    conn = connect(db_path)
    try:
        tickets_data.file_proposal(
            conn, ticket_id, field=FieldName(field), body=body, actor="agent", now=0
        )
    finally:
        conn.close()


def _file_current_proposal(db_path: str, ticket_id: str, body: str) -> None:
    conn = connect(db_path)
    try:
        tickets_data.file_current_proposal_with_recap(
            conn,
            ticket_id,
            body=body,
            recap="Revised result ready for review.",
            actor="agent",
            now=3,
        )
    finally:
        conn.close()


def _needs_review_ticket(db_path: str) -> str:
    tid = _new_ticket(db_path, ceiling=TicketState.needs_review)
    _file_proposal(db_path, tid, "success", "success")
    _file_proposal(db_path, tid, "approach", "approach")
    _file_proposal(db_path, tid, "plan", "plan")
    _file_proposal(db_path, tid, "result", "old result")
    return tid


def _set_key(db_path: str, ticket_id: str, key: str) -> None:
    conn = connect(db_path)
    try:
        tickets_data.finish_run_if_still_running_step(conn, ticket_id, session_key=key, now=0)
    finally:
        conn.close()


def _status_events(db_path: str, ticket_id: str) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = read_events_since(conn, 0, 1000)
    finally:
        conn.close()
    return [
        r.payload
        for r in rows
        if r.entity_id == ticket_id and r.kind == EventKind.ticket_status_changed.value
    ]


def _runner(
    db_path: str,
    fake: FakeGateway,
    doorbell: _RecordingDoorbell | None = None,
) -> EmployeeStepRunner:
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=fake.spawn,
        base_env={},
    )
    return EmployeeStepRunner(
        db_path,
        RealClock(),
        gateway=gateway,
        readiness_doorbell=doorbell or NoOpReadinessDoorbell(),
        boundary_hour=BOUNDARY_HOUR,
    )


def _gateway(fake: FakeGateway) -> SharedGateway:
    return SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=fake.spawn,
        base_env={},
    )


def test_kickoff_parked_proposal_awaits_approval(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    assert _read(db, tid).ticket_status == TicketStatus.empty

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "the success body"),
    )
    runner = _runner(db, fake)
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.awaiting_approval
    assert ticket.chat_session_key == STORED_KEY
    assert ticket.fields.success.proposal is not None
    assert fake.sent_methods() == ["session.create", "prompt.submit"]
    evs = _status_events(db, tid)
    assert [e["ticket_status"] for e in evs] == ["agent_running_step", "awaiting_approval"]
    assert all("worker" not in e for e in evs)


def test_auto_accepted_proposal_completion_clears_to_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling=TicketState.needs_plan)

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "the success body"),
    )
    runner = _runner(db, fake)
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.state == TicketState.needs_approach
    assert ticket.fields.success.value == "the success body"
    assert ticket.fields.success.proposal is None
    assert ticket.ticket_status == TicketStatus.empty


def test_complete_with_no_proposal_is_empty_not_errored(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(_create_script(_complete_ev()))
    doorbell = _RecordingDoorbell()
    runner = _runner(db, fake, doorbell)
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.empty
    assert ticket.chat_session_key == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "empty",
    ]
    assert doorbell.calls == 1


def test_worker_step_prompt_and_reply_are_visible_in_chat_history(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    prompt = (
        f"Work ticket {tid} — T. It is in state 'needs_success'; "
        "take the next step and propose the 'success' field for approval."
    )
    fake = FakeGateway(
        {
            "session.create": [_create_reply()],
            "prompt.submit": [
                _submit_reply(
                    ev(
                        "message.complete",
                        LIVE_SID,
                        {"text": "worker reply", "usage": {}, "status": "complete"},
                    )
                )
            ],
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": STORED_KEY,
                        "messages": [
                            {"role": "user", "content": prompt, "created_at": 1},
                            {"role": "assistant", "content": "worker reply", "created_at": 2},
                        ],
                    }
                )
            ],
        }
    )
    gateway = _gateway(fake)
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        readiness_doorbell=NoOpReadinessDoorbell(),
        boundary_hour=BOUNDARY_HOUR,
    )
    try:
        runner.run_ready_step(tid)
        assert runner.wait_idle(10.0)
        conn = connect(db)
        try:
            history = chat_service.history(conn, gateway, tid, 0)
            state = chat_service.state(conn, gateway, tid, 0)
        finally:
            conn.close()
    finally:
        gateway.shutdown()

    submit_frame = next(frame for frame in fake.sent if frame.get("method") == "prompt.submit")
    assert submit_frame["params"]["text"] == prompt
    assert _read(db, tid).chat_session_key == STORED_KEY
    assert [(msg.role, msg.text) for msg in history.messages] == [
        ("user", prompt),
        ("assistant", "worker reply"),
    ]
    assert [(msg.role, msg.text) for msg in state.messages] == [
        ("worker", prompt),
        ("assistant", "worker reply"),
    ]
    assert state.active_turn is None


def test_claimed_rejection_turn_revises_result_in_same_session_without_chat_copy(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    tid = _needs_review_ticket(db)
    _set_key(db, tid, STORED_KEY)
    fake = _ProposingFake(
        _resume_script(STORED_KEY, _complete_ev()),
        on_submit=lambda: _file_current_proposal(db, tid, "revised result with evidence"),
    )
    gateway = _gateway(fake)
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        readiness_doorbell=NoOpReadinessDoorbell(),
        boundary_hour=BOUNDARY_HOUR,
    )
    try:
        conn = connect(db)
        try:
            ticket = tickets_actions.return_ticket_for_revision(
                conn,
                tid,
                message="Add evidence.",
                actor="human",
                now=1,
                employee_revision_runner=runner,
            )
        finally:
            conn.close()
        assert ticket.state is TicketState.needs_review
        assert ticket.ticket_status is TicketStatus.agent_running_step
        assert runner.wait_idle(10.0)
        conn = connect(db)
        try:
            state = chat_service.state(conn, gateway, tid, 2)
            queues = tickets_views.queues_view(
                conn,
                now=4,
                today_iso="2026-07-09",
                item_approval_rows=[],
                item_overdue_rows=[],
            )
        finally:
            conn.close()
    finally:
        gateway.shutdown()

    submit_frame = next(frame for frame in fake.sent if frame.get("method") == "prompt.submit")
    assert submit_frame["params"]["text"] == (
        "The user rejected your proposal and provided the following guidance:\n\nAdd evidence."
    )
    revised = _read(db, tid)
    assert revised.state is TicketState.needs_review
    assert revised.ticket_status is TicketStatus.awaiting_approval
    assert revised.fields.result.value == "revised result with evidence"
    assert revised.fields.result.proposal is None
    assert [(message.role, message.text) for message in state.messages] == [("assistant", "ok")]
    assert queues["approvals"][0]["waiting_since"] == 3

    conn = connect(db)
    try:
        approved = tickets_data.approve_review(conn, tid, actor="human", now=2)
    finally:
        conn.close()
    assert approved.state is TicketState.done
    assert approved.ticket_status is TicketStatus.empty


def test_created_session_key_is_queryable_before_prompt_submit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    class InspectingFake(FakeGateway):
        def send(self, line: str) -> None:
            frame = json.loads(line)
            if frame.get("method") == "prompt.submit":
                conn = connect(db)
                try:
                    assert tickets_data.read_ticket_by_session_key(conn, STORED_KEY).id == tid
                finally:
                    conn.close()
            super().send(line)

    fake = InspectingFake(_create_script(_complete_ev()))
    runner = _runner(db, fake)

    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    assert _read(db, tid).chat_session_key == STORED_KEY


def test_worker_does_not_prompt_if_session_key_claim_is_lost(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    class ClaimLostGateway:
        prompted = False

        def run_ticket_step(
            self,
            session_key: str | None,
            entity_id: str,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
        ) -> RunResult:
            conn = connect(db)
            try:
                tickets_data.take_over_ticket(conn, tid, now=0)
            finally:
                conn.close()
            if on_session_key is not None:
                on_session_key(STORED_KEY)
            self.prompted = True
            return RunResult("complete", "ok", None, STORED_KEY, None)

    gateway = ClaimLostGateway()
    doorbell = _RecordingDoorbell()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,  # type: ignore[arg-type]
        readiness_doorbell=doorbell,
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert gateway.prompted is False
    assert ticket.ticket_status == TicketStatus.user_takeover
    assert ticket.chat_session_key is None
    assert doorbell.calls == 1


def test_worker_rechecks_existing_session_key_ownership_before_prompt(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _set_key(db, tid, STORED_KEY)

    class ClaimLostGateway:
        prompted = False

        def run_ticket_step(
            self,
            session_key: str | None,
            entity_id: str,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
        ) -> RunResult:
            assert session_key == STORED_KEY
            conn = connect(db)
            try:
                tickets_data.take_over_ticket(conn, tid, now=0)
            finally:
                conn.close()
            if on_session_key is not None:
                on_session_key(STORED_KEY)
            self.prompted = True
            return RunResult("complete", "ok", None, STORED_KEY, None)

    gateway = ClaimLostGateway()
    doorbell = _RecordingDoorbell()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,  # type: ignore[arg-type]
        readiness_doorbell=doorbell,
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert gateway.prompted is False
    assert ticket.ticket_status == TicketStatus.user_takeover
    assert ticket.chat_session_key == STORED_KEY
    assert doorbell.calls == 1


def test_worker_error_does_not_overwrite_lost_ownership(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    class ErrorAfterTakeoverGateway:
        def run_ticket_step(
            self,
            session_key: str | None,
            entity_id: str,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
        ) -> RunResult:
            if on_session_key is not None:
                on_session_key(STORED_KEY)
            conn = connect(db)
            try:
                tickets_data.take_over_ticket(conn, tid, now=0)
            finally:
                conn.close()
            return RunResult("errored", "", None, STORED_KEY, "boom")

    gateway = ErrorAfterTakeoverGateway()
    doorbell = _RecordingDoorbell()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,  # type: ignore[arg-type]
        readiness_doorbell=doorbell,
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.user_takeover
    assert ticket.chat_session_key == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "user_takeover",
    ]
    assert doorbell.calls == 1


def test_gateway_error_event_errors(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(_create_script(ev("error", LIVE_SID, {"message": "boom"})))
    doorbell = _RecordingDoorbell()
    runner = _runner(db, fake, doorbell)
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.errored
    evs = _status_events(db, tid)
    assert evs[-1]["ticket_status"] == "errored"
    assert evs[-1]["error"] == "boom"
    assert doorbell.calls == 1


def test_interrupted_gateway_result_rings_after_errored_settlement(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    doorbell = _RecordingDoorbell()
    runner = _runner(db, FakeGateway(_create_script(_complete_ev(status="interrupted"))), doorbell)

    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    assert _read(db, tid).ticket_status is TicketStatus.errored
    assert doorbell.calls == 1


def test_gateway_busy_4009_is_skip_not_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(
        {"session.create": [_create_reply()], "prompt.submit": [Reply(error=(4009, "busy"))]}
    )
    doorbell = _RecordingDoorbell()
    runner = _runner(db, fake, doorbell)
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.empty
    assert ticket.chat_session_key == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "empty",
    ]
    assert doorbell.calls == 1


def test_concurrent_same_ticket_runs_only_one_prompt(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    reached = threading.Event()
    release = threading.Event()

    class _BlockingFake(FakeGateway):
        def send(self, line: str) -> None:
            if json.loads(line).get("method") == "prompt.submit":
                reached.set()
                release.wait(10.0)
            super().send(line)

    fake = _BlockingFake(_create_script(_complete_ev()))
    doorbell = _RecordingDoorbell()
    runner = _runner(db, fake, doorbell)
    runner.run_ready_step(tid)
    assert reached.wait(10.0)
    assert doorbell.calls == 0  # claiming is not a settlement
    runner.run_ready_step(tid)
    time.sleep(0.2)
    assert doorbell.calls == 0  # the losing duplicate claim does not ring
    release.set()
    assert runner.wait_idle(10.0)

    assert fake.sent_methods().count("prompt.submit") == 1
    assert _read(db, tid).ticket_status == TicketStatus.empty
    assert doorbell.calls == 1


def test_existing_key_is_resumed_and_rotated_tip_persisted(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _set_key(db, tid, STORED_KEY)

    fake = _ProposingFake(
        _resume_script("rotated-key", _complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "body"),
    )
    runner = _runner(db, fake)
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    resume_frame = next(f for f in fake.sent if f.get("method") == "session.resume")
    assert resume_frame["params"]["session_id"] == STORED_KEY
    assert _read(db, tid).chat_session_key == "rotated-key"


def test_spawn_crash_errors_never_stuck_running(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    def _boom(argv: list[str], env: dict[str, str]) -> Any:
        raise RuntimeError("spawn boom")

    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=_boom,
        base_env={},
    )
    doorbell = _RecordingDoorbell()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        readiness_doorbell=doorbell,
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    assert _read(db, tid).ticket_status == TicketStatus.errored
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "errored",
    ]
    assert doorbell.calls == 1


def test_runner_rechecks_today_membership_before_claim(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    conn = connect(db)
    try:
        today_id = dates.resolve_day_id("today", RealClock().now(), BOUNDARY_HOUR)
        days_data.remove_day_ticket(conn, today_id, tid, 0)
    finally:
        conn.close()

    fake = FakeGateway({})
    doorbell = _RecordingDoorbell()
    runner = _runner(db, fake, doorbell)
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.state == TicketState.needs_success
    assert ticket.ticket_status == TicketStatus.empty
    assert fake.sent_methods() == []
    assert _status_events(db, tid) == []
    assert doorbell.calls == 0


def test_runner_rechecks_readiness_predicate_before_claim(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    conn = connect(db)
    try:
        today_id = dates.resolve_day_id("today", RealClock().now(), BOUNDARY_HOUR)
        assert conn.execute(
            "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
            (today_id, tid),
        ).fetchone()
        assert readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid))
        tickets_data.change_scope(
            conn,
            tid,
            ceiling=TicketState.needs_success,
            at_cap=AtCap.stop,
            actor="human",
            now=1,
        )
        assert not readiness.is_runnable(conn, tickets_data.read_ticket(conn, tid))
    finally:
        conn.close()

    fake = FakeGateway({})
    doorbell = _RecordingDoorbell()
    runner = _runner(db, fake, doorbell)
    runner.run_ready_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.state == TicketState.needs_success
    assert ticket.ticket_status == TicketStatus.empty
    assert fake.sent_methods() == []
    assert _status_events(db, tid) == []
    assert doorbell.calls == 0


def test_cancelled_revision_reservation_drains_without_submitting(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    fake = FakeGateway({})
    doorbell = _RecordingDoorbell()
    runner = _runner(db, fake, doorbell)

    handoff = runner.reserve_revision(tid, "revise it")
    handoff.cancel()

    assert runner.wait_idle(10.0)
    assert fake.sent_methods() == []
    assert _read(db, tid).ticket_status is TicketStatus.empty
    assert doorbell.calls == 0


def test_stop_rejects_new_reservations_and_drains_accepted_parked_work(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    runner = _runner(db, FakeGateway({}))
    handoff = runner.reserve_revision(tid, "revise it")
    stopped = threading.Event()

    stop_thread = threading.Thread(target=lambda: (runner.stop(), stopped.set()))
    stop_thread.start()
    assert not stopped.wait(0.2)
    with pytest.raises(PlannerError) as caught:
        runner.reserve_revision(tid, "too late")
    assert caught.value.code is ErrorCode.gateway_offline

    handoff.cancel()
    stop_thread.join(10.0)

    assert stopped.is_set()
    assert runner.wait_idle(0)


def test_stop_waits_for_released_revision_run_and_rejects_new_work(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    tid = _needs_review_ticket(db)
    _set_key(db, tid, STORED_KEY)
    prompt_reached = threading.Event()
    finish_prompt = threading.Event()

    class BlockingRevisionGateway(FakeGateway):
        def send(self, line: str) -> None:
            if json.loads(line).get("method") == "prompt.submit":
                prompt_reached.set()
                assert finish_prompt.wait(10.0)
            super().send(line)

    fake = BlockingRevisionGateway(_resume_script(STORED_KEY, _complete_ev()))
    gateway = _gateway(fake)
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        readiness_doorbell=NoOpReadinessDoorbell(),
        boundary_hour=BOUNDARY_HOUR,
    )
    stop_thread: threading.Thread | None = None
    stopped = threading.Event()
    try:
        conn = connect(db)
        try:
            ticket = tickets_actions.return_ticket_for_revision(
                conn,
                tid,
                message="Add evidence.",
                actor="human",
                now=1,
                employee_revision_runner=runner,
            )
        finally:
            conn.close()
        assert ticket.ticket_status is TicketStatus.agent_running_step
        assert prompt_reached.wait(10.0)

        def stop_runner() -> None:
            runner.stop()
            stopped.set()

        stop_thread = threading.Thread(target=stop_runner)
        stop_thread.start()
        assert not stopped.wait(0.2)
        with pytest.raises(PlannerError) as caught:
            runner.reserve_revision(tid, "too late")
        assert caught.value.code is ErrorCode.gateway_offline

        finish_prompt.set()
        stop_thread.join(10.0)

        assert stopped.is_set()
        assert runner.wait_idle(0)
        assert fake.sent_methods().count("prompt.submit") == 1
    finally:
        finish_prompt.set()
        if stop_thread is not None:
            stop_thread.join(10.0)
        runner.stop()
        gateway.shutdown()
