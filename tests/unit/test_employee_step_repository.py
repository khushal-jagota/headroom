from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from planner.core.db import connect, create_schema
from planner.core.events import read_events_since
from planner.runtime.employee_step_repository import (
    EmployeeStepRun,
    SqliteEmployeeStepRepository,
)
from planner.tickets import data as tickets_data


def _database(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    path = str(tmp_path / "steps.db")
    conn = connect(path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Step",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    return conn, ticket.id


def test_employee_step_record_is_exact_correctness_state() -> None:
    assert tuple(field.name for field in dataclasses.fields(EmployeeStepRun)) == (
        "employee_step_id",
        "ticket_id",
        "status",
        "employee_session_id",
        "error",
        "started_at",
        "updated_at",
        "completed_at",
    )
    assert not {
        "prompt",
        "reply",
        "text",
        "transcript",
        "usage",
        "activity",
        "image",
        "clarification",
    } & {field.name for field in dataclasses.fields(EmployeeStepRun)}


def test_start_bind_and_first_wins_settlement(tmp_path: Path) -> None:
    conn, ticket_id = _database(tmp_path)
    repository = SqliteEmployeeStepRepository()
    conn.execute("BEGIN IMMEDIATE")
    step = repository.start(conn, ticket_id, now=2)
    conn.execute("COMMIT")
    assert repository.read_running(conn, ticket_id) == step
    assert repository.bind_session(
        conn,
        step.employee_step_id,
        ticket_id=ticket_id,
        employee_session_id="session-1",
        now=3,
    ).employee_session_id == "session-1"
    settled = repository.settle(
        conn,
        step.employee_step_id,
        ticket_id=ticket_id,
        status="complete",
        error=None,
        now=4,
    )
    assert settled is not None and settled.status == "complete"
    assert repository.settle(
        conn,
        step.employee_step_id,
        ticket_id=ticket_id,
        status="errored",
        error="late",
        now=5,
    ) is None
    events = read_events_since(conn, 0, 100)
    started = [event for event in events if event.kind == "employee_step_started"]
    assert len(started) == 1
    assert started[0].payload == {"employee_step_id": step.employee_step_id}


def test_one_running_step_and_restart_replacement(tmp_path: Path) -> None:
    conn, ticket_id = _database(tmp_path)
    repository = SqliteEmployeeStepRepository()
    first = repository.start(
        conn, ticket_id, now=2, employee_session_id="session-1"
    )
    with pytest.raises(sqlite3.IntegrityError):
        repository.start(conn, ticket_id, now=3)
    conn.execute("BEGIN IMMEDIATE")
    replacement = repository.replace_running_for_restart(
        conn,
        ticket_id,
        expected_employee_session_id="session-1",
        now=4,
    )
    conn.execute("COMMIT")
    assert replacement is not None
    assert replacement.employee_step_id != first.employee_step_id
    assert replacement.employee_session_id == "session-1"
    assert repository.require(conn, first.employee_step_id).status == "interrupted"
    assert repository.replace_running_for_restart(
        conn,
        ticket_id,
        expected_employee_session_id="wrong-session",
        now=5,
    ) is None


def test_terminal_history_cascades_with_ticket(tmp_path: Path) -> None:
    conn, ticket_id = _database(tmp_path)
    repository = SqliteEmployeeStepRepository()
    step = repository.start(conn, ticket_id, now=2)
    repository.settle(
        conn,
        step.employee_step_id,
        ticket_id=ticket_id,
        status="interrupted",
        error=None,
        now=3,
    )
    tickets_data.delete_ticket(conn, ticket_id, actor="human", now=4)
    assert conn.execute("SELECT 1 FROM employee_step_runs").fetchone() is None
