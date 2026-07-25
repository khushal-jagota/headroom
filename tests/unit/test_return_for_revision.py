from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.step_gateway import EmployeeStepRunResult
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, EmployeeSessionIdTransition
from planner.tickets.data import (
    change_scope,
    create_ticket,
    file_proposal,
    finish_run_if_still_running_step,
)
from planner.tickets.logic import fields_codec

_AGENT = {"X-Plan-Actor": "agent"}


class _RevisionGateway:
    def __init__(self, db_path: Path, *, available: bool = True) -> None:
        self.db_path = db_path
        self.available = available
        self.prompts: list[str] = []

    def run_ticket_step(
        self,
        employee_session_id: str | None,
        entity_id: str,
        prompt_text: str,
        on_employee_session_id=None,
        *,
        require_existing_session: bool = False,
    ) -> EmployeeStepRunResult:
        assert require_existing_session
        assert employee_session_id is not None
        self.prompts.append(prompt_text)
        if on_employee_session_id is not None:
            on_employee_session_id(employee_session_id)
        conn = connect(str(self.db_path))
        try:
            tickets_data.file_proposal(
                conn,
                entity_id,
                field="plan",
                body="revised plan",
                actor="agent",
                now=2,
            )
        finally:
            conn.close()
        return EmployeeStepRunResult("complete", employee_session_id, None)

    def interrupt(
        self,
        employee_session_id: str,
        entity_id: str,
        *,
        deadline: float | None = None,
    ) -> None:
        del employee_session_id, entity_id, deadline

    def status(self) -> object:
        return type("Status", (), {"available": self.available})()


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))
    return app, db_path


def _ticket_with_pending_plan(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Revise plan",
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
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        change_scope(
            conn,
            ticket.id,
            ceiling="needs_plan",
            at_cap=AtCap.propose,
            actor="human",
            now=0,
        )
        file_proposal(conn, ticket.id, field="success", body="success", actor="agent", now=0)
        file_proposal(conn, ticket.id, field="approach", body="approach", actor="agent", now=0)
        file_proposal(conn, ticket.id, field="plan", body="bad plan", actor="agent", now=0)
        finish_run_if_still_running_step(
            conn,
            ticket.id,
            employee_session_transition=EmployeeSessionIdTransition(
                None, f"session-{ticket.id}"
            ),
            now=0,
        )
        return ticket.id
    finally:
        conn.close()


def _install_runner(app: FastAPI, db_path: Path, gateway: _RevisionGateway) -> EmployeeStepRunner:
    runner = EmployeeStepRunner(
        str(db_path),
        app.state.clock,
        gateway=gateway,
        boundary_hour=app.state.config.boundary_hour,
    )
    app.state.employee_step_runner = runner
    return runner


def _wait_idle(runner: EmployeeStepRunner) -> None:
    assert runner.wait_idle(10.0)


def test_revision_delivers_guidance_through_acp_and_settles_correctness_row(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)
    gateway = _RevisionGateway(db_path)
    runner = _install_runner(app, db_path, gateway)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Make the plan concrete."},
        )

    assert response.status_code == 200, response.text
    _wait_idle(runner)
    assert len(gateway.prompts) == 1
    assert "Make the plan concrete." in gateway.prompts[0]
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        runs = conn.execute(
            "SELECT status, employee_session_id, error FROM employee_step_runs "
            "WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchall()
    finally:
        conn.close()
    proposal = fields_codec.get_slot(ticket.fields, "plan").proposal
    assert proposal is not None and proposal.body == "revised plan"
    assert [(row["status"], row["employee_session_id"], row["error"]) for row in runs] == [
        ("complete", f"session-{ticket_id}", None)
    ]


def test_running_employee_step_rejects_revision_before_ticket_mutation(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)
    conn = connect(str(db_path))
    try:
        SqliteEmployeeStepRepository().start(conn, ticket_id, now=1)
        before = tickets_data.read_ticket(conn, ticket_id)
    finally:
        conn.close()
    runner = _install_runner(app, db_path, _RevisionGateway(db_path))

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Try another plan."},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_running"
    _wait_idle(runner)
    conn = connect(str(db_path))
    try:
        assert tickets_data.read_ticket(conn, ticket_id) == before
    finally:
        conn.close()


def test_revision_requires_existing_session_and_available_runner(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET employee_session_id = NULL WHERE id = ?", (ticket_id,))
    finally:
        conn.close()
    runner = _install_runner(app, db_path, _RevisionGateway(db_path))

    with TestClient(app) as client:
        missing_session = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Revise it."},
        )
    assert missing_session.status_code == 400
    assert missing_session.json()["error"]["code"] == "validation"
    _wait_idle(runner)

    app.state.employee_step_runner = None
    with TestClient(app) as client:
        unavailable = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "Revise it."},
        )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "gateway_offline"


def test_return_for_revision_agent_forbidden_and_requires_message(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket_with_pending_plan(db_path)
    _install_runner(app, db_path, _RevisionGateway(db_path))

    with TestClient(app) as client:
        agent = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "x"},
            headers=_AGENT,
        )
        empty = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision", json={"message": ""}
        )

    assert agent.status_code == 400
    assert agent.json()["error"]["code"] == "agent_forbidden"
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "validation"
