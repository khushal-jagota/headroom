from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.chat import data as chat_data
from planner.chat import service as chat_service
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import EventKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.shared_gateway import SharedGateway
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.readiness_doorbell import NoOpReadinessDoorbell
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, FieldName, TicketState
from planner.tickets.data import (
    change_scope,
    create_ticket,
    file_proposal,
    finish_run_if_still_running_step,
)
from planner.tickets.logic import fields_codec
from planner.worker_context import data as worker_context_data
from planner.worker_context.service import SqliteWorkerContextService

_AGENT = {"X-Plan-Actor": "agent"}


class _RecordingDoorbell:
    def __init__(self) -> None:
        self.calls = 0

    def ring(self) -> None:
        self.calls += 1


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _install_real_runner(
    app: FastAPI,
    db_path: Path,
    fake: FakeGateway,
    *,
    hermes_python: str = sys.executable,
    worker_context: SqliteWorkerContextService | None = None,
    doorbell: _RecordingDoorbell | None = None,
) -> tuple[EmployeeStepRunner, SharedGateway]:
    gateway = SharedGateway(
        hermes_python=hermes_python,
        home="/tmp/planner-home",
        worker_role="planning-worker",
        spawn=fake.spawn,
        base_env={},
        worker_context=worker_context,
    )
    runner = EmployeeStepRunner(
        str(db_path),
        app.state.clock,
        gateway=gateway,
        readiness_doorbell=doorbell or NoOpReadinessDoorbell(),
        boundary_hour=app.state.config.boundary_hour,
    )
    app.state.employee_step_runner = runner
    return runner, gateway


def _durable_snapshot(db_path: Path, ticket_id: str) -> tuple[tuple[tuple[Any, ...], ...], ...]:
    conn = connect(str(db_path))
    try:
        queries = (
            ("SELECT * FROM tickets WHERE id = ?", (ticket_id,)),
            ("SELECT * FROM events WHERE entity_id = ? ORDER BY id", (ticket_id,)),
            (
                "SELECT * FROM pending_worker_context "
                "WHERE worker_entity_id = ? ORDER BY context_key",
                (ticket_id,),
            ),
            ("SELECT * FROM chat_turns WHERE entity_id = ? ORDER BY id", (ticket_id,)),
            ("SELECT * FROM chat_messages WHERE entity_id = ? ORDER BY id", (ticket_id,)),
        )
        return tuple(
            tuple(tuple(row) for row in conn.execute(sql, params).fetchall())
            for sql, params in queries
        )
    finally:
        conn.close()


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path


def _ticket_with_pending_plan(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(conn, title="Revise plan", actor="human", now=0, title_max_chars=200)
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field=FieldName.kickoff,
            actor="human",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        change_scope(
            conn,
            ticket.id,
            ceiling=TicketState.needs_plan,
            at_cap=AtCap.propose,
            actor="human",
            now=0,
        )
        file_proposal(
            conn, ticket.id, field=FieldName.success, body="success", actor="agent", now=0
        )
        file_proposal(
            conn, ticket.id, field=FieldName.approach, body="approach", actor="agent", now=0
        )
        file_proposal(
            conn, ticket.id, field=FieldName.plan, body="bad plan", actor="agent", now=0
        )
        finish_run_if_still_running_step(
            conn, ticket.id, session_key=f"session-{ticket.id}", now=0
        )
    finally:
        conn.close()
    return ticket.id


def _ticket_with_pending_closeout(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn, title="Revise closeout", actor="human", now=0, title_max_chars=200
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field=FieldName.kickoff,
            actor="human",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        change_scope(
            conn,
            ticket.id,
            ceiling=TicketState.needs_closeout,
            at_cap=AtCap.propose,
            actor="human",
            now=0,
        )
        file_proposal(
            conn, ticket.id, field=FieldName.success, body="success", actor="agent", now=0
        )
        file_proposal(
            conn, ticket.id, field=FieldName.approach, body="approach", actor="agent", now=0
        )
        file_proposal(conn, ticket.id, field=FieldName.plan, body="plan", actor="agent", now=0)
        file_proposal(
            conn,
            ticket.id,
            field=FieldName.implementation,
            body="implementation",
            actor="agent",
            now=0,
        )
        file_proposal(
            conn,
            ticket.id,
            field=FieldName.closeout,
            body="bad closeout",
            actor="agent",
            now=0,
        )
        finish_run_if_still_running_step(
            conn, ticket.id, session_key=f"session-{ticket.id}", now=0
        )
    finally:
        conn.close()
    return ticket.id


def test_http_revision_uses_real_runner_without_readiness_loop_and_returns_before_completion(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    stored_key = f"session-{tid}"
    prompt_reached = threading.Event()
    finish_prompt = threading.Event()

    class BlockingRevisionGateway(FakeGateway):
        def send(self, line: str) -> None:
            frame = json.loads(line)
            if frame.get("method") == "prompt.submit":
                prompt_reached.set()
                assert finish_prompt.wait(10.0)
                conn = connect(str(db_path))
                try:
                    tickets_data.file_proposal(
                        conn,
                        tid,
                        field=FieldName.plan,
                        body="revised plan",
                        actor="agent",
                        now=3,
                    )
                finally:
                    conn.close()
            super().send(line)

    fake = BlockingRevisionGateway(
        {
            "session.resume": [
                Reply(result={"session_id": "live-session", "resumed": stored_key})
            ],
            "prompt.submit": [
                Reply(
                    result={"status": "streaming"},
                    events_after=(
                        ev(
                            "message.complete",
                            "live-session",
                            {
                                "text": "Revised plan ready.",
                                "usage": {},
                                "status": "complete",
                            },
                        ),
                    ),
                )
            ],
        }
    )
    doorbell = _RecordingDoorbell()
    runner, gateway = _install_real_runner(app, db_path, fake, doorbell=doorbell)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{tid}/return-for-revision",
                json={"message": "Make it shorter."},
            )
            assert response.status_code == 200, response.json()
            assert prompt_reached.wait(10.0)

            before_completion = client.get(f"/api/tickets/{tid}").json()
            events = client.get(f"/api/tickets/{tid}/events").json()["events"]
            approvals = client.get("/api/queues").json()["approvals"]

            assert before_completion["ticket_status"] == "agent_running_step"
            assert before_completion["fields"]["plan"]["proposal"] is None
            assert approvals == []
            assert doorbell.calls == 0
            claimed = [
                event
                for event in events
                if event["kind"] == EventKind.ticket_status_changed.value
                and event["payload"].get("ticket_status") == "agent_running_step"
            ]
            assert len(claimed) == 1
            conn = connect(str(db_path))
            try:
                assert conn.execute(
                    "SELECT role, text FROM chat_messages WHERE entity_id = ? ORDER BY id",
                    (tid,),
                ).fetchall() == []
            finally:
                conn.close()

            finish_prompt.set()
            assert runner.wait_idle(10.0)
            settled = client.get(f"/api/tickets/{tid}").json()

        assert settled["state"] == "needs_plan"
        assert settled["ticket_status"] == "awaiting_approval"
        assert settled["fields"]["plan"]["proposal"]["body"] == "revised plan"
        assert doorbell.calls == 1
        assert fake.sent_methods() == ["session.resume", "prompt.submit"]
        resume = next(frame for frame in fake.sent if frame["method"] == "session.resume")
        submit = next(frame for frame in fake.sent if frame["method"] == "prompt.submit")
        assert resume["params"]["session_id"] == stored_key
        assert submit["params"]["text"] == (
            "The user rejected your proposal and provided the following guidance:"
            "\n\nMake it shorter."
        )
        conn = connect(str(db_path))
        try:
            assert [
                (str(row["role"]), str(row["text"]))
                for row in conn.execute(
                    "SELECT role, text FROM chat_messages WHERE entity_id = ? ORDER BY id",
                    (tid,),
                ).fetchall()
            ] == [("assistant", "Revised plan ready.")]
            turn = conn.execute(
                "SELECT origin, status FROM chat_turns WHERE entity_id = ?", (tid,)
            ).fetchone()
            assert turn is not None
            assert (turn["origin"], turn["status"]) == ("worker", "complete")
        finally:
            conn.close()
    finally:
        finish_prompt.set()
        runner.wait_idle(10.0)
        gateway.shutdown()


def test_return_for_revision_clears_proposal_after_accepting_employee_handoff(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    employee_runner = app.state.employee_step_runner

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "Make it shorter."},
        )
        assert response.status_code == 200, response.json()
        ticket = response.json()
        chat = client.get(f"/api/chat/{tid}/state").json()
        events = client.get(f"/api/tickets/{tid}/events").json()["events"]
        approvals = client.get("/api/queues").json()["approvals"]
        duplicate = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "Duplicate send."},
        )

    assert ticket["state"] == "needs_plan"
    assert ticket["ticket_status"] == "agent_running_step"
    assert ticket["fields"]["plan"]["value"] is None
    assert ticket["fields"]["plan"]["proposal"] is None
    assert chat["messages"] == []
    assert all(event["kind"] != "approval_returned" for event in events)
    assert _wait_until(lambda: len(employee_runner.decisions) == 2)
    assert employee_runner.decisions == [
        (tid, "Make it shorter.", "released"),
        (tid, "Duplicate send.", "cancelled"),
    ]
    assert approvals == []
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "already_running"


def test_return_for_revision_keeps_closeout_gate_after_accepted_handoff(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_closeout(db_path)
    employee_runner = app.state.employee_step_runner

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "The closeout needs evidence."},
        )
        assert response.status_code == 200, response.json()
        ticket = response.json()
        events = client.get(f"/api/tickets/{tid}/events").json()["events"]

    assert ticket["state"] == "needs_closeout"
    assert ticket["ticket_status"] == "agent_running_step"
    assert ticket["fields"]["closeout"]["value"] is None
    assert ticket["fields"]["closeout"]["proposal"] is None
    assert all(event["kind"] != "approval_returned" for event in events)
    assert all(
        event["payload"].get("cause") != "return_for_revision"
        for event in events
        if event["kind"] == "state_changed"
    )
    assert _wait_until(lambda: len(employee_runner.decisions) == 1)
    assert employee_runner.decisions == [
        (tid, "The closeout needs evidence.", "released")
    ]
    with TestClient(app) as client:
        assert client.get("/api/queues").json()["approvals"] == []


def test_return_for_revision_requires_existing_worker_session(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET chat_session_key = NULL WHERE id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "Revise it."},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_db_validation_after_real_reservation_cancels_without_prompt(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET chat_session_key = NULL WHERE id = ?", (tid,))
    finally:
        conn.close()
    fake = FakeGateway({})
    doorbell = _RecordingDoorbell()
    runner, gateway = _install_real_runner(app, db_path, fake, doorbell=doorbell)
    before = _durable_snapshot(db_path, tid)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{tid}/return-for-revision",
                json={"message": "Revise it."},
            )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation"
        assert runner.wait_idle(10.0)
        assert fake.sent_methods() == []
        assert _durable_snapshot(db_path, tid) == before
        assert doorbell.calls == 0
    finally:
        gateway.shutdown()


def test_running_human_chat_turn_rejects_revision_before_ticket_mutation(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    conn = connect(str(db_path))
    try:
        chat_data.start_turn(
            conn,
            tid,
            origin="human",
            mode="message",
            visible_role="human",
            visible_text="I am already talking to the employee.",
            output_role="assistant",
            phase="thinking",
            activity_label="Thinking",
            now=1,
        )
    finally:
        conn.close()
    fake = FakeGateway({})
    doorbell = _RecordingDoorbell()
    runner, gateway = _install_real_runner(app, db_path, fake, doorbell=doorbell)
    before = _durable_snapshot(db_path, tid)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{tid}/return-for-revision",
                json={"message": "Try a different plan."},
            )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_running"
        assert runner.wait_idle(10.0)
        assert fake.sent_methods() == []
        assert _durable_snapshot(db_path, tid) == before
        assert doorbell.calls == 0
    finally:
        gateway.shutdown()


def test_post_commit_worker_turn_collision_errors_claim_without_submitting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    fake = FakeGateway({})
    doorbell = _RecordingDoorbell()
    runner, gateway = _install_real_runner(app, db_path, fake, doorbell=doorbell)
    original_start = chat_service.start_worker_turn

    def collide_with_human_turn(
        conn: Connection, entity_id: str, *, visible_text: str, now: int
    ):
        chat_data.start_turn(
            conn,
            entity_id,
            origin="human",
            mode="message",
            visible_role="human",
            visible_text="Human turn won the race.",
            output_role="assistant",
            phase="thinking",
            activity_label="Thinking",
            now=now,
        )
        return original_start(conn, entity_id, visible_text=visible_text, now=now)

    monkeypatch.setattr(chat_service, "start_worker_turn", collide_with_human_turn)
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{tid}/return-for-revision",
                json={"message": "Try a different plan."},
            )
        assert response.status_code == 200
        assert runner.wait_idle(10.0)
        assert fake.sent_methods() == []
        conn = connect(str(db_path))
        try:
            ticket = tickets_data.read_ticket(conn, tid)
            active = chat_data.read_active_turn(conn, tid)
            messages = conn.execute(
                "SELECT role, text FROM chat_messages WHERE entity_id = ? ORDER BY id", (tid,)
            ).fetchall()
        finally:
            conn.close()
        assert ticket.ticket_status.value == "errored"
        assert active is not None
        assert active.origin == "human"
        assert [(row["role"], row["text"]) for row in messages] == [
            ("human", "Human turn won the race.")
        ]
        assert doorbell.calls == 1
    finally:
        gateway.shutdown()


def test_stale_revision_session_never_remints_and_settles_ticket_errored(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    stale_key = f"session-{tid}"
    conn = connect(str(db_path))
    try:
        worker_context_data.set_context(
            conn,
            tid,
            "ticket_changed",
            "Reread the ticket before continuing.",
        )
        pending_before = [
            (row["context_key"], row["revision"])
            for row in conn.execute(
                "SELECT context_key, revision FROM pending_worker_context "
                "WHERE worker_entity_id = ?",
                (tid,),
            ).fetchall()
        ]
        session_events_before = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE entity_id = ? AND kind = ?",
                (tid, EventKind.chat_session_created.value),
            ).fetchone()["n"]
        )
    finally:
        conn.close()

    class StrictResumeOnlyFake(FakeGateway):
        def send(self, line: str) -> None:
            method = str(json.loads(line).get("method"))
            if method != "session.resume":
                raise AssertionError(f"strict revision unexpectedly called {method}")
            super().send(line)

    fake = StrictResumeOnlyFake(
        {"session.resume": [Reply(error=(4007, "stored session not found"))]}
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    doorbell = _RecordingDoorbell()
    runner, gateway = _install_real_runner(
        app,
        db_path,
        fake,
        worker_context=SqliteWorkerContextService(conn_factory),
        doorbell=doorbell,
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{tid}/return-for-revision",
                json={"message": "Try again without losing our history."},
            )
        assert response.status_code == 200
        assert runner.wait_idle(10.0)
        assert fake.sent_methods() == ["session.resume"]
        conn = connect(str(db_path))
        try:
            ticket = tickets_data.read_ticket(conn, tid)
            turn = conn.execute(
                "SELECT status, error FROM chat_turns WHERE entity_id = ?", (tid,)
            ).fetchone()
            pending = conn.execute(
                "SELECT context_key, revision FROM pending_worker_context "
                "WHERE worker_entity_id = ?",
                (tid,),
            ).fetchall()
            session_events_after = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM events WHERE entity_id = ? AND kind = ?",
                    (tid, EventKind.chat_session_created.value),
                ).fetchone()["n"]
            )
        finally:
            conn.close()
        assert ticket.ticket_status.value == "errored"
        assert ticket.chat_session_key == stale_key
        assert fields_codec.get_slot(ticket.fields, "plan").proposal is None
        assert turn is not None and turn["status"] == "errored"
        assert [(row["context_key"], row["revision"]) for row in pending] == pending_before
        assert session_events_after == session_events_before
        assert doorbell.calls == 1
    finally:
        gateway.shutdown()


@pytest.mark.parametrize("runner_state", ["missing", "stopped", "gateway_unavailable"])
def test_unavailable_employee_runner_is_503_and_changes_nothing(
    tmp_path: Path, runner_state: str
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    gateway: SharedGateway | None = None
    if runner_state == "missing":
        app.state.employee_step_runner = None
    else:
        runner, gateway = _install_real_runner(
            app,
            db_path,
            FakeGateway({}),
            hermes_python=(
                "/definitely/missing/hermes-python"
                if runner_state == "gateway_unavailable"
                else sys.executable
            ),
        )
        if runner_state == "stopped":
            runner.stop()
    before = _durable_snapshot(db_path, tid)

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{tid}/return-for-revision",
                json={"message": "Do not lose this guidance."},
            )
    finally:
        if gateway is not None:
            gateway.shutdown()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_offline"
    assert _durable_snapshot(db_path, tid) == before


def test_return_for_revision_agent_forbidden_and_requires_message(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)

    with TestClient(app) as client:
        agent = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "x"},
            headers=_AGENT,
        )
        empty = client.post(f"/api/tickets/{tid}/return-for-revision", json={"message": ""})

    assert agent.status_code == 400
    assert agent.json()["error"]["code"] == "agent_forbidden"
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "validation"
