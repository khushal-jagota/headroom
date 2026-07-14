"""EmployeeStepRunner against a hermetic shared fake gateway."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from planner.chat import data as chat_data
from planner.chat import service as chat_service
from planner.chat.contracts import GatewayStatus
from planner.core import links as core_links
from planner.core.clock import RealClock
from planner.core.clock import TestClock as MutableClock
from planner.core.contracts import EventKind, LinkKind
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import read_events_since
from planner.days import data as days_data
from planner.days.logic import dates
from planner.minds.contracts import OnEvent, RunResult
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.shared_gateway import SharedGateway
from planner.runtime import automatic_employee_step_eligibility
from planner.runtime.automatic_employee_step_eligibility_wake import (
    NoOpAutomaticEmployeeStepEligibilityWake,
)
from planner.runtime.employee_step_runner import EmployeeStepRunner, _next_step_prompt
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets import employee_session_history
from planner.tickets import views as tickets_views
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    EmployeeSessionIdTransition,
    ExecutionRoute,
    TicketStatus,
)
from planner.tickets.logic import fields_codec
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.contracts import WorkerTypeDefinition

HOME = "/tmp/planner-home"
HERMES_PY = sys.executable
LIVE_SID = "live-sid"
STORED_KEY = "stored-key-1"
ROLE = "planning-worker"
BOUNDARY_HOUR = 5
AVAILABLE_GATEWAY_STATUS = GatewayStatus(available=True)


def _delete_kickoff_setup_events(conn: sqlite3.Connection, ticket_id: str) -> None:
    conn.execute(
        "DELETE FROM events WHERE entity_id = ? AND kind IN ("
        "'proposal_filed', 'proposal_accepted', 'stage_changed', "
        "'ticket_status_changed')",
        (ticket_id,),
    )


def _accept_kickoff_field(conn: sqlite3.Connection, ticket_id: str):
    return tickets_data.accept_proposal(
        conn,
        ticket_id,
        field="kickoff",
        actor="human",
        now=0,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )


class _RecordingEligibilityWake:
    def __init__(self) -> None:
        self.calls = 0

    def wake(self) -> None:
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


def _new_ticket(
    db_path: str,
    *,
    ceiling: str | None = None,
    execution_route: ExecutionRoute | None = None,
) -> str:
    conn = connect(db_path)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="T",
            actor="human",
            now=0,
            title_max_chars=200,
            execution_route=execution_route,
        )
        ticket = _accept_kickoff_field(conn, ticket.id)
        _delete_kickoff_setup_events(conn, ticket.id)
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
        tickets_data.file_proposal(conn, ticket_id, field=field, body=body, actor="agent", now=0)
    finally:
        conn.close()


def _file_current_proposal(db_path: str, ticket_id: str, body: str) -> None:
    conn = connect(db_path)
    try:
        tickets_data.file_current_proposal_with_recap(
            conn,
            ticket_id,
            body=body,
            recap="Revised closeout ready for review.",
            actor="agent",
            now=3,
        )
    finally:
        conn.close()


def _needs_closeout_ticket(db_path: str) -> str:
    tid = _new_ticket(db_path, ceiling="needs_closeout")
    _file_proposal(db_path, tid, "success", "success")
    _file_proposal(db_path, tid, "approach", "approach")
    _file_proposal(db_path, tid, "plan", "plan")
    _file_proposal(db_path, tid, "implementation", "implementation")
    _file_proposal(db_path, tid, "closeout", "old closeout")
    _set_key(db_path, tid, STORED_KEY)
    return tid


def _set_key(db_path: str, ticket_id: str, key: str) -> None:
    conn = connect(db_path)
    try:
        tickets_data.finish_run_if_still_running_step(
            conn,
            ticket_id,
            employee_session_transition=EmployeeSessionIdTransition(None, key),
            now=0,
        )
    finally:
        conn.close()


def _claim_automatic_for_recovery(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int = 1,
):
    return tickets_data.claim_automatic_employee_step(
        conn,
        ticket_id,
        planning_day_id_resolver=lambda: dates.resolve_day_id(
            "today", RealClock().now(), BOUNDARY_HOUR
        ),
        eligibility_check=(
            automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
        ),
        now=now,
    )


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
    eligibility_wake: _RecordingEligibilityWake | None = None,
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
        automatic_employee_step_eligibility_wake=eligibility_wake
        or NoOpAutomaticEmployeeStepEligibilityWake(),
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
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.awaiting_approval
    assert ticket.employee_session_id == STORED_KEY
    assert fields_codec.get_slot(ticket.fields, "success").proposal is not None
    assert fake.sent_methods() == ["session.create", "prompt.submit"]
    evs = _status_events(db, tid)
    assert [e["ticket_status"] for e in evs] == ["agent_running_step", "awaiting_approval"]
    assert all("worker" not in e for e in evs)


def test_pending_kickoff_is_rechecked_before_runner_claim(tmp_path: Path) -> None:
    db = _db(tmp_path)
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Pending kickoff",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        today_id = dates.resolve_day_id("today", RealClock().now(), BOUNDARY_HOUR)
        days_data.add_day_ticket(conn, today_id, ticket.id, 0)
    finally:
        conn.close()

    fake = _ProposingFake(_create_script(_complete_ev()), on_submit=lambda: None)
    runner = _runner(db, fake)
    runner.try_run_automatic_step(ticket.id)
    assert runner.wait_idle(10.0)

    unchanged = _read(db, ticket.id)
    assert unchanged.stage == "needs_kickoff"
    assert unchanged.ticket_status is TicketStatus.awaiting_approval
    assert fake.sent_methods() == []
    statuses = [event["ticket_status"] for event in _status_events(db, ticket.id)]
    assert "agent_running_step" not in statuses


def test_runner_injects_the_exact_complete_eligibility_function(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    captured: dict[str, Any] = {}

    def capture_claim(
        conn: sqlite3.Connection,
        claimed_ticket_id: str,
        **kwargs: Any,
    ) -> None:
        captured.update(kwargs)
        assert claimed_ticket_id == ticket_id
        assert conn.in_transaction is False
        return None

    monkeypatch.setattr(tickets_data, "claim_automatic_employee_step", capture_claim)
    fake = FakeGateway({})
    runner = _runner(db, fake)

    runner.try_run_automatic_step(ticket_id)
    assert runner.wait_idle(10.0)

    assert captured["eligibility_check"] is (
        automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
    )
    assert callable(captured["planning_day_id_resolver"])
    assert isinstance(captured["now"], int)
    assert fake.sent_methods() == []


def test_auto_accepted_proposal_completion_clears_to_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db, ceiling="needs_plan")

    fake = _ProposingFake(
        _create_script(_complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "the success body"),
    )
    runner = _runner(db, fake)
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.stage == "needs_approach"
    assert fields_codec.get_slot(ticket.fields, "success").value == "the success body"
    assert fields_codec.get_slot(ticket.fields, "success").proposal is None
    assert ticket.ticket_status == TicketStatus.empty


def test_complete_with_no_proposal_is_empty_not_errored(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(_create_script(_complete_ev()))
    eligibility_wake = _RecordingEligibilityWake()
    runner = _runner(db, fake, eligibility_wake)
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.empty
    assert ticket.employee_session_id == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "empty",
    ]
    assert eligibility_wake.calls == 1


def test_next_step_prompt_includes_execution_route_and_stage_owner(tmp_path: Path) -> None:
    db = _db(tmp_path)
    conn = connect(db)
    try:
        assigned = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="T",
            actor="human",
            now=0,
            title_max_chars=200,
            execution_route=ExecutionRoute.hermes_claude,
        )
        assigned = _accept_kickoff_field(conn, assigned.id)
        _delete_kickoff_setup_events(conn, assigned.id)
        unassigned = tickets_data.create_ticket(
            conn, worker_type="coding", title="T", actor="human", now=0, title_max_chars=200
        )
        unassigned = _accept_kickoff_field(conn, unassigned.id)
        _delete_kickoff_setup_events(conn, unassigned.id)
    finally:
        conn.close()

    worker_type_definition = configured_worker_type_registry().require("coding")
    assert _next_step_prompt(assigned, worker_type_definition=worker_type_definition) == (
        f"Work ticket {assigned.id} — T. It is at Stage 'needs_success'; "
        "take the next step and propose the 'success' field for approval. "
        "Execution route: hermes_claude. Stage owner: worker."
    )
    assert _next_step_prompt(unassigned, worker_type_definition=worker_type_definition) == (
        f"Work ticket {unassigned.id} — T. It is at Stage 'needs_success'; "
        "take the next step and propose the 'success' field for approval. "
        "Execution route: unassigned. Stage owner: worker."
    )


def test_next_step_prompt_reads_novel_stage_field_for_new_worker(tmp_path: Path) -> None:
    # Regression: _next_step_prompt resolves the ticket's OWN type definition, so a
    # new_worker ticket at the NOVEL needs_stages stage names the 'stages' field instead
    # of raising 'Stage outside the linear order' (which it would if the gating field
    # were resolved against the coding default).
    db = _db(tmp_path)
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            title="Design a worker",
            actor="human",
            now=0,
            title_max_chars=200,
            worker_type="new_worker",
        )
        # Accept kickoff -> advances to the novel needs_stages stage.
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
    finally:
        conn.close()

    assert ticket.stage == "needs_stages"
    assert _next_step_prompt(
        ticket,
        worker_type_definition=configured_worker_type_registry().require(ticket.worker_type),
    ) == (
        f"Work ticket {ticket.id} — Design a worker. It is at Stage 'needs_stages'; "
        "take the next step and propose the 'stages' field for approval. "
        "Execution route: unassigned. Stage owner: worker."
    )


def test_worker_step_prompt_and_reply_are_visible_in_chat_history(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db, execution_route=ExecutionRoute.hermes_codex)
    prompt = (
        f"Work ticket {tid} — T. It is at Stage 'needs_success'; "
        "take the next step and propose the 'success' field for approval. "
        "Execution route: hermes_codex. Stage owner: worker."
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
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    try:
        runner.try_run_automatic_step(tid)
        assert runner.wait_idle(10.0)
        conn = connect(db)
        try:
            history = employee_session_history.read_employee_session_history(
                conn, gateway, tid, 0
            )
            state = chat_service.state(conn, tid)
        finally:
            conn.close()
    finally:
        gateway.shutdown()

    submit_frame = next(frame for frame in fake.sent if frame.get("method") == "prompt.submit")
    assert submit_frame["params"]["text"] == prompt
    assert _read(db, tid).employee_session_id == STORED_KEY
    assert [(msg.role, msg.text) for msg in history.messages] == [
        ("user", prompt),
        ("assistant", "worker reply"),
    ]
    assert [(msg.role, msg.text) for msg in state.messages] == [
        ("worker", prompt),
        ("assistant", "worker reply"),
    ]
    assert state.active_turn is None


def test_queued_employee_waits_past_prior_interruption_before_settling(
    tmp_path: Path,
) -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    db = _db(tmp_path)
    tid = _new_ticket(db)
    _set_key(db, tid, STORED_KEY)
    prompt = (
        f"Work ticket {tid} — T. It is at Stage 'needs_success'; "
        "take the next step and propose the 'success' field for approval. "
        "Execution route: unassigned. Stage owner: worker."
    )
    fake = ManualEventFake(
        {
            "session.resume": [
                Reply(
                    result={
                        "session_id": LIVE_SID,
                        "resumed": STORED_KEY,
                        "running": True,
                    }
                )
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "queued"},
                    events_after=(
                        ev("message.delta", LIVE_SID, {"text": "old delta"}),
                        ev("tool.start", LIVE_SID, {"name": "old tool"}),
                        ev(
                            "message.complete",
                            LIVE_SID,
                            {
                                "text": "prior partial",
                                "usage": {},
                                "status": "interrupted",
                            },
                        ),
                    ),
                )
            ],
        }
    )
    gateway = _gateway(fake)
    eligibility_wake = _RecordingEligibilityWake()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        automatic_employee_step_eligibility_wake=eligibility_wake,
        boundary_hour=BOUNDARY_HOUR,
    )

    try:
        runner.try_run_automatic_step(tid)
        assert fake.wait_sent(2, 5.0)
        time.sleep(0.05)
        conn = connect(db)
        try:
            in_flight_state = chat_service.state(conn, tid)
        finally:
            conn.close()
        assert _read(db, tid).ticket_status is TicketStatus.agent_running_step
        assert in_flight_state.active_turn is not None
        assert in_flight_state.active_turn.output_text == ""
        assert in_flight_state.active_turn.phase == "thinking"
        assert in_flight_state.active_turn.activity_label == "Thinking"
        assert eligibility_wake.calls == 0

        fake.emit(ev("message.start", LIVE_SID))
        fake.emit(ev("message.delta", LIVE_SID, {"text": "employee"}))
        fake.emit(
            ev(
                "message.complete",
                LIVE_SID,
                {"text": "employee reply", "usage": {}, "status": "complete"},
            )
        )
        assert runner.wait_idle(5.0)

        conn = connect(db)
        try:
            settled_state = chat_service.state(conn, tid)
        finally:
            conn.close()
    finally:
        gateway.shutdown()

    assert _read(db, tid).ticket_status is TicketStatus.empty
    assert settled_state.active_turn is None
    assert [(message.role, message.text) for message in settled_state.messages] == [
        ("worker", prompt),
        ("assistant", "employee reply"),
    ]
    assert eligibility_wake.calls == 1


def test_claimed_rejection_turn_revises_closeout_in_same_session_without_chat_copy(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    tid = _needs_closeout_ticket(db)
    fake = _ProposingFake(
        _resume_script(STORED_KEY, _complete_ev()),
        on_submit=lambda: _file_current_proposal(db, tid, "revised closeout with evidence"),
    )
    gateway = _gateway(fake)
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
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
        assert ticket.stage == "needs_closeout"
        assert ticket.ticket_status is TicketStatus.agent_running_step
        assert runner.wait_idle(10.0)
        conn = connect(db)
        try:
            state = chat_service.state(conn, tid)
            day_id = "day_2026-07-09"
            days_data.add_day_ticket(conn, day_id, tid, 4)
            review = tickets_views.review_view(conn, day_id=day_id)
        finally:
            conn.close()
    finally:
        gateway.shutdown()

    submit_frame = next(frame for frame in fake.sent if frame.get("method") == "prompt.submit")
    assert submit_frame["params"]["text"] == (
        "The user rejected your proposal and provided the following guidance:\n\nAdd evidence."
    )
    revised = _read(db, tid)
    assert revised.stage == "needs_closeout"
    assert revised.ticket_status is TicketStatus.awaiting_approval
    assert fields_codec.get_slot(revised.fields, "closeout").value is None
    assert fields_codec.get_slot(revised.fields, "closeout").proposal is not None
    assert (
        fields_codec.get_slot(revised.fields, "closeout").proposal.body
        == "revised closeout with evidence"
    )
    assert [(message.role, message.text) for message in state.messages] == [("assistant", "ok")]
    assert review["ticket_decisions"][0]["waiting_since"] == 3

    conn = connect(db)
    try:
        approved = tickets_data.accept_proposal(
            conn,
            tid,
            field="closeout",
            actor="human",
            now=2,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
    finally:
        conn.close()
    assert approved.stage == "done"
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
                    assert (
                        tickets_data.read_ticket_by_employee_session_id(conn, STORED_KEY).id
                        == tid
                    )
                finally:
                    conn.close()
            super().send(line)

    fake = InspectingFake(_create_script(_complete_ev()))
    runner = _runner(db, fake)

    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    assert _read(db, tid).employee_session_id == STORED_KEY


def test_active_takeover_during_new_session_run_settles_to_user_takeover(
    tmp_path: Path,
) -> None:
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
    eligibility_wake = _RecordingEligibilityWake()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=eligibility_wake,
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert gateway.prompted is True
    assert ticket.ticket_status == TicketStatus.user_takeover
    assert ticket.employee_session_id == STORED_KEY
    assert eligibility_wake.calls == 1


def test_active_takeover_during_existing_session_run_settles_to_user_takeover(
    tmp_path: Path,
) -> None:
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
    eligibility_wake = _RecordingEligibilityWake()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=eligibility_wake,
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert gateway.prompted is True
    assert ticket.ticket_status == TicketStatus.user_takeover
    assert ticket.employee_session_id == STORED_KEY
    assert eligibility_wake.calls == 1


def test_worker_error_after_active_takeover_leaves_ticket_errored(tmp_path: Path) -> None:
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
    eligibility_wake = _RecordingEligibilityWake()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=eligibility_wake,
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.errored
    assert ticket.employee_session_id == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "errored",
    ]
    assert eligibility_wake.calls == 1


def test_gateway_error_event_errors(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(_create_script(ev("error", LIVE_SID, {"message": "boom"})))
    eligibility_wake = _RecordingEligibilityWake()
    runner = _runner(db, fake, eligibility_wake)
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.errored
    evs = _status_events(db, tid)
    assert evs[-1]["ticket_status"] == "errored"
    assert evs[-1]["error"] == "boom"
    assert eligibility_wake.calls == 1


def test_interrupted_gateway_result_wakes_after_errored_settlement(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    eligibility_wake = _RecordingEligibilityWake()
    runner = _runner(
        db, FakeGateway(_create_script(_complete_ev(status="interrupted"))), eligibility_wake
    )

    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    assert _read(db, tid).ticket_status is TicketStatus.errored
    assert eligibility_wake.calls == 1


def test_service_stop_interruption_leaves_ticket_recoverable(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    entered = threading.Event()
    release = threading.Event()
    interrupt_calls: list[tuple[str, str, float | None]] = []

    class InterruptingDuringStopGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            session_key: str | None,
            entity_id: str,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
            *,
            require_existing_session: bool = False,
        ) -> RunResult:
            if on_session_key is not None:
                on_session_key(STORED_KEY)
            entered.set()
            assert release.wait(5.0)
            return RunResult("interrupted", "partial stop text", None, STORED_KEY, None)

        def interrupt(
            self,
            session_key: str,
            entity_id: str,
            *,
            deadline: float | None = None,
        ) -> None:
            interrupt_calls.append((session_key, entity_id, deadline))
            release.set()

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=InterruptingDuringStopGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )

    runner.try_run_automatic_step(ticket_id)
    assert entered.wait(5.0)
    deadline = time.monotonic() + 5.0
    runner.stop(deadline=deadline)

    assert interrupt_calls == [(STORED_KEY, ticket_id, deadline)]
    ticket = _read(db, ticket_id)
    assert ticket.ticket_status is TicketStatus.agent_running_step
    assert ticket.employee_session_id == STORED_KEY
    conn = connect(db)
    try:
        active = chat_data.read_active_turn(conn, ticket_id)
        assert active is None
        row = conn.execute(
            "SELECT status, output_text FROM chat_turns WHERE entity_id = ?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()
    assert (row["status"], row["output_text"]) == ("interrupted", "partial stop text")


def test_stop_does_not_invent_missing_employee_session_id(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    entered = threading.Event()
    release = threading.Event()
    interrupt_calls: list[tuple[str, str]] = []

    class NotYetBoundGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(self, *_args, **_kwargs) -> RunResult:
            entered.set()
            assert release.wait(5.0)
            return RunResult("interrupted", "", None, None, None)

        def interrupt(self, session_key: str, entity_id: str, **_kwargs) -> None:
            interrupt_calls.append((session_key, entity_id))

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=NotYetBoundGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(ticket_id)
    assert entered.wait(5.0)

    runner.stop(deadline=time.monotonic() + 0.05)
    assert interrupt_calls == []
    release.set()
    assert runner.wait_idle(5.0)


def test_stop_does_not_interrupt_parked_revision_reservation(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_id = _needs_closeout_ticket(db)
    interrupt_calls: list[tuple[str, str]] = []

    class ParkedGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def interrupt(self, session_key: str, entity_id: str, **_kwargs) -> None:
            interrupt_calls.append((session_key, entity_id))

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=ParkedGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    handoff = runner.reserve_revision(ticket_id, "revise")

    runner.stop(deadline=time.monotonic() + 0.05)
    assert interrupt_calls == []
    handoff.cancel()
    assert runner.wait_idle(5.0)


def test_stop_attempts_each_bound_session_when_one_interrupt_fails(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_ids = [_new_ticket(db), _new_ticket(db)]
    entered: set[str] = set()
    entered_cond = threading.Condition()
    releases = {ticket_id: threading.Event() for ticket_id in ticket_ids}
    interrupt_calls: list[str] = []

    class PartlyFailingGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self, _session_key, entity_id, _prompt, _on_event=None, on_session_key=None, **_kwargs
        ) -> RunResult:
            session_key = f"session-{entity_id}"
            assert on_session_key is not None
            on_session_key(session_key)
            with entered_cond:
                entered.add(entity_id)
                entered_cond.notify_all()
            assert releases[entity_id].wait(5.0)
            return RunResult("interrupted", "", None, session_key, None)

        def interrupt(self, _session_key: str, entity_id: str, **_kwargs) -> None:
            interrupt_calls.append(entity_id)
            releases[entity_id].set()
            if entity_id == sorted(ticket_ids)[0]:
                raise RuntimeError("first interrupt failed")

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=PartlyFailingGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    for ticket_id in ticket_ids:
        runner.try_run_automatic_step(ticket_id)
    with entered_cond:
        assert entered_cond.wait_for(lambda: len(entered) == 2, timeout=5.0)

    runner.stop(deadline=time.monotonic() + 5.0)
    assert interrupt_calls == sorted(ticket_ids)


def test_concurrent_stop_interrupts_each_bound_session_once(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    entered = threading.Event()
    interrupt_entered = threading.Event()
    release_interrupt = threading.Event()
    interrupt_calls: list[str] = []

    class BlockingInterruptGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            _session_key,
            entity_id,
            _prompt,
            _on_event=None,
            on_session_key=None,
            **_kwargs,
        ):
            assert on_session_key is not None
            on_session_key(STORED_KEY)
            entered.set()
            assert release_interrupt.wait(5.0)
            return RunResult("interrupted", "", None, STORED_KEY, None)

        def interrupt(self, _session_key: str, entity_id: str, **_kwargs) -> None:
            interrupt_calls.append(entity_id)
            interrupt_entered.set()
            assert release_interrupt.wait(5.0)

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=BlockingInterruptGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(ticket_id)
    assert entered.wait(5.0)
    deadline = time.monotonic() + 5.0
    stoppers = [threading.Thread(target=lambda: runner.stop(deadline=deadline)) for _ in range(2)]
    stoppers[0].start()
    assert interrupt_entered.wait(5.0)
    stoppers[1].start()
    time.sleep(0.05)
    release_interrupt.set()
    for stopper in stoppers:
        stopper.join(5.0)

    assert all(not stopper.is_alive() for stopper in stoppers)
    assert interrupt_calls == [ticket_id]


def test_stop_interrupt_wait_uses_shared_deadline(tmp_path: Path) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    entered = threading.Event()
    release = threading.Event()
    received_deadlines: list[float | None] = []

    class DeadlineGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            _session_key,
            _entity_id,
            _prompt,
            _on_event=None,
            on_session_key=None,
            **_kwargs,
        ):
            assert on_session_key is not None
            on_session_key(STORED_KEY)
            entered.set()
            assert release.wait(5.0)
            return RunResult("interrupted", "", None, STORED_KEY, None)

        def interrupt(self, _session_key: str, _entity_id: str, *, deadline=None) -> None:
            received_deadlines.append(deadline)
            assert deadline is not None
            release.wait(max(0.0, deadline - time.monotonic()))
            raise RuntimeError("interrupt timed out")

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=DeadlineGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(ticket_id)
    assert entered.wait(5.0)
    deadline = time.monotonic() + 0.1
    started = time.monotonic()
    runner.stop(deadline=deadline)
    elapsed = time.monotonic() - started

    assert received_deadlines == [deadline]
    assert elapsed < 0.25
    release.set()
    assert runner.wait_idle(5.0)


def test_expired_stop_does_not_wait_for_locked_shutdown_settlement(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    entered = threading.Event()
    release = threading.Event()
    interrupt_calls: list[tuple[str, str, float | None]] = []

    class LockedSettlementGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            _session_key,
            entity_id,
            _prompt,
            on_event=None,
            on_session_key=None,
            **_kwargs,
        ):
            assert on_session_key is not None
            assert on_event is not None
            on_session_key(STORED_KEY)
            on_event(ev("message.delta", LIVE_SID, {"text": "partial"})["params"])
            entered.set()
            assert release.wait(5.0)
            return RunResult("interrupted", "late output", None, STORED_KEY, None)

        def interrupt(
            self,
            session_key: str,
            entity_id: str,
            *,
            deadline: float | None = None,
        ) -> None:
            interrupt_calls.append((session_key, entity_id, deadline))

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=LockedSettlementGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
        busy_timeout_ms=200,
    )
    runner.try_run_automatic_step(ticket_id)
    assert entered.wait(5.0)

    writer = connect(db)
    stop_finished = threading.Event()
    stop_errors: list[BaseException] = []
    stop_elapsed: list[float] = []
    deadline = time.monotonic() - 1.0

    def stop_runner() -> None:
        started = time.monotonic()
        try:
            runner.stop(deadline=deadline)
        except BaseException as exc:  # noqa: BLE001 - assertion surface
            stop_errors.append(exc)
        finally:
            stop_elapsed.append(time.monotonic() - started)
            stop_finished.set()

    stopper = threading.Thread(target=stop_runner)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE tickets SET title = title WHERE id = ?", (ticket_id,))
        stopper.start()

        assert stop_finished.wait(0.1) is True
        assert stop_errors == []
        assert stop_elapsed[0] < 0.1
        assert interrupt_calls == [(STORED_KEY, ticket_id, deadline)]
        ticket_row = writer.execute(
            "SELECT ticket_status, employee_session_id FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        turn_row = writer.execute(
            "SELECT status, session_key FROM chat_turns WHERE entity_id = ?",
            (ticket_id,),
        ).fetchone()
        assert tuple(ticket_row) == ("agent_running_step", STORED_KEY)
        assert tuple(turn_row) == ("running", STORED_KEY)
    finally:
        writer.rollback()
        stopper.join(5.0)
        writer.close()
        release.set()

    assert runner.wait_idle(5.0)
    ticket = _read(db, ticket_id)
    assert ticket.ticket_status is TicketStatus.agent_running_step
    assert ticket.employee_session_id == STORED_KEY


def test_expired_stop_settles_matched_snapshot_turn_before_returning(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    entered = threading.Event()
    release = threading.Event()
    interrupt_calls: list[tuple[str, str, float | None]] = []

    class ExpiredDeadlineGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            _session_key,
            entity_id,
            _prompt,
            on_event=None,
            on_session_key=None,
            **_kwargs,
        ):
            assert on_session_key is not None
            assert on_event is not None
            on_session_key(STORED_KEY)
            on_event(ev("message.delta", LIVE_SID, {"text": "partial before stop"})["params"])
            entered.set()
            assert release.wait(5.0)
            return RunResult("interrupted", "late output", None, STORED_KEY, None)

        def interrupt(
            self,
            session_key: str,
            entity_id: str,
            *,
            deadline: float | None = None,
        ) -> None:
            interrupt_calls.append((session_key, entity_id, deadline))

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=ExpiredDeadlineGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(ticket_id)
    assert entered.wait(5.0)
    deadline = time.monotonic() - 1.0

    runner.stop(deadline=deadline)

    assert interrupt_calls == [(STORED_KEY, ticket_id, deadline)]
    conn = connect(db)
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        worker_turn = conn.execute(
            "SELECT status, output_text, session_key FROM chat_turns WHERE entity_id = ?",
            (ticket_id,),
        ).fetchone()
    finally:
        conn.close()
    assert ticket.ticket_status is TicketStatus.agent_running_step
    assert ticket.employee_session_id == STORED_KEY
    assert worker_turn is not None
    assert tuple(worker_turn) == ("interrupted", "partial before stop", STORED_KEY)

    release.set()
    assert runner.wait_idle(5.0)
    assert _read(db, ticket_id).ticket_status is TicketStatus.agent_running_step


def test_late_complete_after_expired_stop_does_not_advance_ticket(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    entered = threading.Event()
    release = threading.Event()

    class LateCompleteGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            _session_key,
            _entity_id,
            _prompt,
            on_event=None,
            on_session_key=None,
            **_kwargs,
        ):
            assert on_session_key is not None
            assert on_event is not None
            on_session_key(STORED_KEY)
            on_event(ev("message.delta", LIVE_SID, {"text": "partial before stop"})["params"])
            entered.set()
            assert release.wait(5.0)
            return RunResult("complete", "late complete output", None, STORED_KEY, None)

        def interrupt(self, *_args, **_kwargs) -> None:
            return None

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=LateCompleteGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(ticket_id)
    assert entered.wait(5.0)

    runner.stop(deadline=time.monotonic() - 1.0)
    release.set()
    assert runner.wait_idle(5.0)

    conn = connect(db)
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        worker_turn = conn.execute(
            "SELECT status, output_text, session_key FROM chat_turns WHERE entity_id = ?",
            (ticket_id,),
        ).fetchone()
    finally:
        conn.close()
    assert ticket.ticket_status is TicketStatus.agent_running_step
    assert ticket.employee_session_id == STORED_KEY
    assert worker_turn is not None
    assert tuple(worker_turn) == ("interrupted", "partial before stop", STORED_KEY)


def test_late_complete_after_ordinary_interruption_marks_ticket_errored(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    ticket_id = _new_ticket(db)
    entered = threading.Event()
    release = threading.Event()

    class LateCompleteGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            _session_key,
            _entity_id,
            _prompt,
            on_event=None,
            on_session_key=None,
            **_kwargs,
        ):
            assert on_session_key is not None
            assert on_event is not None
            on_session_key(STORED_KEY)
            on_event(
                ev("message.delta", LIVE_SID, {"text": "partial before pause"})["params"]
            )
            entered.set()
            assert release.wait(5.0)
            return RunResult("complete", "late complete output", None, STORED_KEY, None)

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=LateCompleteGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(ticket_id)
    assert entered.wait(5.0)
    conn = connect(db)
    try:
        active_turn = chat_data.read_active_turn(conn, ticket_id)
        assert active_turn is not None
        chat_data.settle_chat_turn(
            conn,
            active_turn.id,
            entity_id=ticket_id,
            status="interrupted",
            reply_text="",
            output_role="assistant",
            error=None,
            now=RealClock().now_unix(),
        )
    finally:
        conn.close()

    release.set()
    assert runner.wait_idle(5.0)

    conn = connect(db)
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        worker_turn = conn.execute(
            "SELECT status, output_text, session_key FROM chat_turns WHERE entity_id = ?",
            (ticket_id,),
        ).fetchone()
    finally:
        conn.close()
    assert ticket.ticket_status is TicketStatus.errored
    assert ticket.employee_session_id == STORED_KEY
    assert worker_turn is not None
    assert tuple(worker_turn) == ("interrupted", "partial before pause", STORED_KEY)


def test_ordinary_interruption_outside_service_stop_still_errors(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    eligibility_wake = _RecordingEligibilityWake()

    class OrdinaryInterruptedGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            session_key: str | None,
            entity_id: str,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
            *,
            require_existing_session: bool = False,
        ) -> RunResult:
            if on_session_key is not None:
                on_session_key(STORED_KEY)
            return RunResult("interrupted", "ordinary interrupt", None, STORED_KEY, None)

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=OrdinaryInterruptedGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=eligibility_wake,
        boundary_hour=BOUNDARY_HOUR,
    )

    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(5.0)

    assert _read(db, tid).ticket_status is TicketStatus.errored
    assert eligibility_wake.calls == 1


def test_same_process_recovery_admission_for_ticket_submits_once(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    conn = connect(db)
    try:
        claimed = _claim_automatic_for_recovery(conn, tid)
        assert claimed is not None
        tickets_data.claim_running_step_employee_session_id(
            conn,
            tid,
            transition=EmployeeSessionIdTransition(None, STORED_KEY),
            now=2,
        )
        stale_turn = chat_data.start_turn(
            conn,
            tid,
            origin="worker",
            mode="worker_step",
            visible_role="worker",
            visible_text="original prompt",
            output_role="assistant",
            phase="responding",
            activity_label=None,
            now=1,
        )
        chat_data.attach_session_key(
            conn,
            stale_turn.id,
            entity_id=tid,
            session_key=STORED_KEY,
            now=1,
        )
        conn.commit()
    finally:
        conn.close()
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class BlockingRecoveryGateway:
        def status(self):
            return AVAILABLE_GATEWAY_STATUS

        def run_ticket_step(
            self,
            session_key: str | None,
            entity_id: str,
            prompt_text: str,
            on_event: OnEvent | None = None,
            on_session_key: Callable[[str], None] | None = None,
            *,
            require_existing_session: bool = False,
        ) -> RunResult:
            assert session_key == STORED_KEY
            assert require_existing_session is True
            calls.append(entity_id)
            entered.set()
            assert release.wait(5.0)
            return RunResult("complete", "recovered", None, STORED_KEY, None)

    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=BlockingRecoveryGateway(),  # type: ignore[arg-type]
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )

    runner.recover_running_step(tid)
    assert entered.wait(5.0)
    runner.recover_running_step(tid)
    time.sleep(0.05)

    assert calls == [tid]

    release.set()
    assert runner.wait_idle(5.0)
    assert _read(db, tid).ticket_status is TicketStatus.empty


def test_unknown_employee_submit_fails_once_and_ignores_later_completion(
    tmp_path: Path,
) -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    db = _db(tmp_path)
    tid = _new_ticket(db)
    fake = ManualEventFake(
        {
            "session.create": [_create_reply()],
            "prompt.submit": [Reply()],
        }
    )
    gateway = SharedGateway(
        hermes_python=HERMES_PY,
        home=HOME,
        worker_role=ROLE,
        spawn=fake.spawn,
        base_env={},
        request_timeout=0.01,
    )
    eligibility_wake = _RecordingEligibilityWake()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        automatic_employee_step_eligibility_wake=eligibility_wake,
        boundary_hour=BOUNDARY_HOUR,
    )

    try:
        runner.try_run_automatic_step(tid)
        assert runner.wait_idle(5.0)
        conn = connect(db)
        try:
            state_before = chat_service.state(conn, tid)
        finally:
            conn.close()

        fake.emit(_complete_ev())
        time.sleep(0.05)
        conn = connect(db)
        try:
            state_after = chat_service.state(conn, tid)
        finally:
            conn.close()
    finally:
        gateway.shutdown()

    ticket = _read(db, tid)
    assert ticket.ticket_status is TicketStatus.errored
    assert state_before == state_after
    assert state_after.active_turn is None
    assert [event["ticket_status"] for event in _status_events(db, tid)] == [
        "agent_running_step",
        "errored",
    ]
    assert "outcome is unknown" in _status_events(db, tid)[-1]["error"]
    assert fake.sent_methods().count("prompt.submit") == 1
    assert eligibility_wake.calls == 1


def test_steered_employee_submit_errors_without_claiming_active_completion(
    tmp_path: Path,
) -> None:
    class ManualEventFake(FakeGateway):
        def emit(self, event: dict[str, Any]) -> None:
            self._out.put(json.dumps(event))

    db = _db(tmp_path)
    tid = _new_ticket(db)
    fake = ManualEventFake(
        {
            "session.create": [_create_reply()],
            "prompt.submit": [Reply(result={"status": "steered"})],
        }
    )
    gateway = _gateway(fake)
    eligibility_wake = _RecordingEligibilityWake()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        automatic_employee_step_eligibility_wake=eligibility_wake,
        boundary_hour=BOUNDARY_HOUR,
    )

    try:
        runner.try_run_automatic_step(tid)
        assert runner.wait_idle(5.0)
        conn = connect(db)
        try:
            state_before = chat_service.state(conn, tid)
        finally:
            conn.close()

        fake.emit(_complete_ev())
        time.sleep(0.05)
        conn = connect(db)
        try:
            state_after = chat_service.state(conn, tid)
        finally:
            conn.close()
    finally:
        gateway.shutdown()

    assert _read(db, tid).ticket_status is TicketStatus.errored
    assert state_before == state_after
    assert state_after.active_turn is None
    status_events = _status_events(db, tid)
    assert [event["ticket_status"] for event in status_events] == [
        "agent_running_step",
        "errored",
    ]
    assert "steering the active execution" in status_events[-1]["error"]
    assert fake.sent_methods().count("prompt.submit") == 1
    assert eligibility_wake.calls == 1


def test_gateway_busy_4009_is_skip_not_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)

    fake = FakeGateway(
        {"session.create": [_create_reply()], "prompt.submit": [Reply(error=(4009, "busy"))]}
    )
    eligibility_wake = _RecordingEligibilityWake()
    runner = _runner(db, fake, eligibility_wake)
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    ticket = _read(db, tid)
    assert ticket.ticket_status == TicketStatus.empty
    assert ticket.employee_session_id == STORED_KEY
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "empty",
    ]
    assert eligibility_wake.calls == 1


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
    eligibility_wake = _RecordingEligibilityWake()
    runner = _runner(db, fake, eligibility_wake)
    runner.try_run_automatic_step(tid)
    assert reached.wait(10.0)
    assert eligibility_wake.calls == 0  # claiming is not a settlement
    runner.try_run_automatic_step(tid)
    time.sleep(0.2)
    assert eligibility_wake.calls == 0  # the losing duplicate claim does not wake
    release.set()
    assert runner.wait_idle(10.0)

    assert fake.sent_methods().count("prompt.submit") == 1
    assert _read(db, tid).ticket_status == TicketStatus.empty
    assert eligibility_wake.calls == 1


def test_existing_key_is_resumed_and_rotated_tip_persisted(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    _set_key(db, tid, STORED_KEY)

    fake = _ProposingFake(
        _resume_script("rotated-key", _complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "body"),
    )
    runner = _runner(db, fake)
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    resume_frame = next(f for f in fake.sent if f.get("method") == "session.resume")
    assert resume_frame["params"]["session_id"] == STORED_KEY
    assert _read(db, tid).employee_session_id == "rotated-key"


def test_recovery_resumes_running_ticket_session_with_owner_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    conn = connect(db)
    try:
        claimed = _claim_automatic_for_recovery(conn, tid)
        assert claimed is not None
        tickets_data.claim_running_step_employee_session_id(
            conn,
            tid,
            transition=EmployeeSessionIdTransition(None, STORED_KEY),
            now=2,
        )
        stale_turn = chat_service.start_worker_turn(
            conn, tid, visible_text="original worker prompt", now=2
        )
        chat_service.attach_worker_session_key(conn, tid, stale_turn.id, STORED_KEY, 2)
        chat_data.append_turn_output(
            conn, stale_turn.id, entity_id=tid, delta="partial draft", now=3
        )
    finally:
        conn.close()
    monkeypatch.setattr(
        "planner.runtime.employee_step_runner._next_step_prompt",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("original prompt replayed")
        ),
    )
    fake = _ProposingFake(
        _resume_script(STORED_KEY, _complete_ev()),
        on_submit=lambda: _file_proposal(db, tid, "success", "recovered success"),
    )
    runner = _runner(db, fake)

    runner.recover_running_step(tid)
    assert runner.wait_idle(10.0)

    submit_frame = next(frame for frame in fake.sent if frame.get("method") == "prompt.submit")
    recovery_message = submit_frame["params"]["text"]
    assert fake.sent_methods() == ["session.resume", "prompt.submit"]
    assert submit_frame["params"]["session_id"] == LIVE_SID
    assert "Panels restarted" in recovery_message
    assert "inspect the canonical ticket" in recovery_message
    assert "existing conversation" in recovery_message
    assert "continue unfinished work" in recovery_message
    assert "avoid repeating completed actions" in recovery_message
    assert "file the currently requested proposal" in recovery_message
    assert "if you already filed that proposal" in recovery_message
    assert "only say so in Chat" in recovery_message
    assert "Work ticket" not in recovery_message
    recovered = _read(db, tid)
    assert recovered.ticket_status is TicketStatus.awaiting_approval
    assert recovered.employee_session_id == STORED_KEY
    assert fields_codec.get_slot(recovered.fields, "success").proposal is not None
    conn = connect(db)
    try:
        active_turn_count = conn.execute(
            "SELECT COUNT(*) FROM chat_turns WHERE entity_id = ? AND status = 'running'",
            (tid,),
        ).fetchone()[0]
        messages = conn.execute(
            "SELECT role, text FROM chat_messages WHERE entity_id = ? ORDER BY id",
            (tid,),
        ).fetchall()
    finally:
        conn.close()
    assert active_turn_count == 0
    assert [(message["role"], message["text"]) for message in messages] == [
        ("worker", "original worker prompt"),
        ("assistant", "partial draft"),
        ("system", recovery_message),
        ("assistant", "ok"),
    ]


def test_recovery_without_employee_session_errors_stale_worker_turn_without_gateway(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    conn = connect(db)
    try:
        claimed = _claim_automatic_for_recovery(conn, tid)
        assert claimed is not None
        assert claimed.employee_session_id is None
        stale_turn = chat_service.start_worker_turn(
            conn, tid, visible_text="original worker prompt", now=2
        )
    finally:
        conn.close()
    fake = FakeGateway({})
    runner = _runner(db, fake)

    runner.recover_running_step(tid)
    assert runner.wait_idle(10.0)

    recovered = _read(db, tid)
    assert recovered.ticket_status is TicketStatus.errored
    assert recovered.employee_session_id is None
    assert fake.sent_methods() == []
    conn = connect(db)
    try:
        active_turn = chat_data.read_active_turn(conn, tid)
        turn = conn.execute(
            "SELECT status, error FROM chat_turns WHERE id = ?", (stale_turn.id,)
        ).fetchone()
    finally:
        conn.close()
    assert active_turn is None
    assert turn is not None
    assert turn["status"] == "errored"
    assert turn["error"] == "restart recovery has no existing Employee session"


def test_recovery_rejects_stale_worker_turn_session_mismatch_without_gateway(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    conn = connect(db)
    try:
        claimed = _claim_automatic_for_recovery(conn, tid)
        assert claimed is not None
        tickets_data.claim_running_step_employee_session_id(
            conn,
            tid,
            transition=EmployeeSessionIdTransition(None, STORED_KEY),
            now=2,
        )
        stale_turn = chat_service.start_worker_turn(
            conn, tid, visible_text="original worker prompt", now=2
        )
        chat_service.attach_worker_session_key(
            conn, tid, stale_turn.id, "wrong-session", 2
        )
    finally:
        conn.close()
    fake = FakeGateway({})
    runner = _runner(db, fake)

    runner.recover_running_step(tid)
    assert runner.wait_idle(10.0)

    recovered = _read(db, tid)
    assert recovered.ticket_status is TicketStatus.errored
    assert recovered.employee_session_id == STORED_KEY
    assert fake.sent_methods() == []
    conn = connect(db)
    try:
        turn = conn.execute(
            "SELECT status, error FROM chat_turns WHERE id = ?", (stale_turn.id,)
        ).fetchone()
        recovery_messages = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE entity_id = ? AND role = 'system'",
            (tid,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert turn is not None and turn["status"] == "errored"
    assert "session key does not match" in str(turn["error"])
    assert recovery_messages == 0


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
    eligibility_wake = _RecordingEligibilityWake()
    runner = EmployeeStepRunner(
        db,
        RealClock(),
        gateway=gateway,
        automatic_employee_step_eligibility_wake=eligibility_wake,
        boundary_hour=BOUNDARY_HOUR,
    )
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    assert _read(db, tid).ticket_status == TicketStatus.errored
    assert [e["ticket_status"] for e in _status_events(db, tid)] == [
        "agent_running_step",
        "errored",
    ]
    assert eligibility_wake.calls == 1


@pytest.mark.parametrize(
    "stale_mutation",
    [
        "day_removed",
        "non_empty_status",
        "terminal_stage",
        "non_terminal_stage_without_gated_field",
        "parked_proposal",
        "scope_stop_at_cap",
        "active_blocker",
        "active_chat_turn",
    ],
)
def test_stale_discovery_rechecks_every_eligibility_conjunct_before_side_effects(
    tmp_path: Path,
    stale_mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    conn = connect(db)
    try:
        today_id = dates.resolve_day_id("today", RealClock().now(), BOUNDARY_HOUR)
        ticket = tickets_data.read_ticket(conn, tid)
        definition = configured_worker_type_registry().require(ticket.worker_type)
        assert automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step(
            conn,
            ticket,
            planning_day_id=today_id,
            worker_type_definition=definition,
        )
        if stale_mutation == "day_removed":
            days_data.remove_day_ticket(conn, today_id, tid, 1)
        elif stale_mutation == "non_empty_status":
            conn.execute("UPDATE tickets SET ticket_status = 'user_takeover' WHERE id = ?", (tid,))
        elif stale_mutation == "terminal_stage":
            conn.execute("UPDATE tickets SET stage = 'done' WHERE id = ?", (tid,))
        elif stale_mutation == "non_terminal_stage_without_gated_field":
            ungated_definition = replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=tuple(
                    replace(stage, gating_field=None) if stage.id == ticket.stage else stage
                    for stage in CODING_WORKER_TYPE_DEFINITION.stages
                ),
            )
            assert not ungated_definition.is_terminal(ticket.stage)
            assert ungated_definition.gating_field(ticket.stage) is None

            class RegistryWithUngatedCurrentStage:
                def require(self, worker_type: str) -> WorkerTypeDefinition:
                    assert worker_type == ticket.worker_type
                    return ungated_definition

            monkeypatch.setattr(
                tickets_data,
                "configured_worker_type_registry",
                lambda: RegistryWithUngatedCurrentStage(),
            )
        elif stale_mutation == "parked_proposal":
            tickets_data.file_proposal(
                conn,
                tid,
                field="success",
                body="parked after discovery",
                actor="agent",
                now=1,
            )
        elif stale_mutation == "scope_stop_at_cap":
            tickets_data.change_scope(
                conn,
                tid,
                ceiling="needs_success",
                at_cap=AtCap.stop,
                actor="human",
                now=1,
            )
        elif stale_mutation == "active_blocker":
            blocker_id = _new_ticket(db)
            core_links.add_link(conn, blocker_id, tid, LinkKind.blocks, 1)
        else:
            chat_data.start_turn(
                conn,
                tid,
                origin="human",
                mode="message",
                visible_role="human",
                visible_text="still chatting",
                output_role="assistant",
                phase="thinking",
                activity_label="Thinking",
                now=1,
            )

        baseline_ticket = tickets_data.read_ticket(conn, tid)
        baseline_status_events = _status_events(db, tid)
        baseline_chat_messages = conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE entity_id = ?", (tid,)
        ).fetchone()[0]
        baseline_chat_turns = conn.execute(
            "SELECT COUNT(*) FROM chat_turns WHERE entity_id = ?", (tid,)
        ).fetchone()[0]
    finally:
        conn.close()

    fake = FakeGateway({})
    eligibility_wake = _RecordingEligibilityWake()
    runner = _runner(db, fake, eligibility_wake)
    runner.try_run_automatic_step(tid)
    assert runner.wait_idle(10.0)

    conn = connect(db)
    try:
        assert tickets_data.read_ticket(conn, tid) == baseline_ticket
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE entity_id = ?", (tid,)
            ).fetchone()[0]
            == baseline_chat_messages
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM chat_turns WHERE entity_id = ?", (tid,)).fetchone()[
                0
            ]
            == baseline_chat_turns
        )
    finally:
        conn.close()
    assert _status_events(db, tid) == baseline_status_events
    if stale_mutation == "non_terminal_stage_without_gated_field":
        assert _read(db, tid).ticket_status is TicketStatus.empty
        assert not any(
            event["ticket_status"] == TicketStatus.agent_running_step.value
            for event in _status_events(db, tid)
        )
    assert fake.sent_methods() == []
    assert eligibility_wake.calls == 0


def test_runner_resolves_today_inside_claim_transaction_across_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_boundary = datetime(2026, 7, 10, 4, 59, 59).astimezone()
    after_boundary = datetime(2026, 7, 10, 5, 0, 0).astimezone()
    clock = MutableClock(before_boundary)
    old_today_id = dates.resolve_day_id("today", before_boundary, BOUNDARY_HOUR)
    new_today_id = dates.resolve_day_id("today", after_boundary, BOUNDARY_HOUR)
    assert old_today_id != new_today_id

    db = _db(tmp_path)
    conn = connect(db)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Boundary ticket",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        ticket = _accept_kickoff_field(conn, ticket.id)
        _delete_kickoff_setup_events(conn, ticket.id)
        days_data.add_day_ticket(conn, old_today_id, ticket.id, 0)
    finally:
        conn.close()

    before_start_run = threading.Event()
    release_start_run = threading.Event()
    real_start_run = tickets_data.claim_automatic_employee_step

    def start_run_after_boundary(*args: Any, **kwargs: Any) -> Any:
        before_start_run.set()
        assert release_start_run.wait(10.0)
        return real_start_run(*args, **kwargs)

    monkeypatch.setattr(tickets_data, "claim_automatic_employee_step", start_run_after_boundary)
    fake = FakeGateway(_create_script(_complete_ev()))
    gateway = _gateway(fake)
    runner = EmployeeStepRunner(
        db,
        clock,
        gateway=gateway,
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
        boundary_hour=BOUNDARY_HOUR,
    )
    try:
        runner.try_run_automatic_step(ticket.id)
        assert before_start_run.wait(10.0)
        clock.set(after_boundary)
        release_start_run.set()
        assert runner.wait_idle(10.0)

        conn = connect(db)
        try:
            stored = tickets_data.read_ticket(conn, ticket.id)
            chat_turn_count = conn.execute(
                "SELECT COUNT(*) FROM chat_turns WHERE entity_id = ?", (ticket.id,)
            ).fetchone()[0]
            chat_message_count = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE entity_id = ?", (ticket.id,)
            ).fetchone()[0]
        finally:
            conn.close()

        assert stored.ticket_status is TicketStatus.empty
        assert _status_events(db, ticket.id) == []
        assert chat_turn_count == 0
        assert chat_message_count == 0
        assert fake.sent_methods() == []
    finally:
        release_start_run.set()
        runner.stop()
        gateway.shutdown()


def test_cancelled_revision_reservation_drains_without_submitting(tmp_path: Path) -> None:
    db = _db(tmp_path)
    tid = _new_ticket(db)
    fake = FakeGateway({})
    eligibility_wake = _RecordingEligibilityWake()
    runner = _runner(db, fake, eligibility_wake)

    handoff = runner.reserve_revision(tid, "revise it")
    handoff.cancel()

    assert runner.wait_idle(10.0)
    assert fake.sent_methods() == []
    assert _read(db, tid).ticket_status is TicketStatus.empty
    assert eligibility_wake.calls == 0


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
    tid = _needs_closeout_ticket(db)
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
        automatic_employee_step_eligibility_wake=NoOpAutomaticEmployeeStepEligibilityWake(),
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
