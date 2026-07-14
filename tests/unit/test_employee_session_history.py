"""Ticket-owned Employee session history and identity contract."""

from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import fields
from pathlib import Path

import pytest

from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    EmployeeSessionHistory,
    EmployeeSessionHistoryMessage,
    EmployeeSessionIdTransition,
)
from planner.tickets.employee_session_history import read_employee_session_history


def test_employee_session_public_contract_is_exact() -> None:
    from planner.core.adapters.base import GatewayAdapter
    from planner.tickets.contracts import (
        EmployeeSessionHistory,
        EmployeeSessionHistoryMessage,
        EmployeeSessionIdTransition,
    )
    from planner.tickets.employee_session_history import read_employee_session_history

    assert [field.name for field in fields(EmployeeSessionIdTransition)] == [
        "expected_employee_session_id",
        "candidate_employee_session_id",
    ]
    assert [field.name for field in fields(EmployeeSessionHistoryMessage)] == [
        "role",
        "text",
        "created_at",
    ]
    assert [field.name for field in fields(EmployeeSessionHistory)] == [
        "messages",
        "employee_session_id",
    ]
    assert list(inspect.signature(read_employee_session_history).parameters) == [
        "conn",
        "gateway",
        "ticket_id",
        "now",
    ]
    assert list(inspect.signature(GatewayAdapter.read_employee_session_history).parameters) == [
        "self",
        "employee_session_id",
        "ticket_id",
    ]
    assert not hasattr(GatewayAdapter, "history")


def _ticket_db(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    conn = connect(str(tmp_path / "history.db"))
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        title="History ticket",
        actor="human",
        now=1,
        title_max_chars=200,
        worker_type="coding",
    )
    return conn, ticket.id


def _set_employee_session(
    conn: sqlite3.Connection, ticket_id: str, value: str, now: int = 2
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    tickets_data.write_employee_session_id_in_transaction(
        conn,
        ticket_id,
        transition=EmployeeSessionIdTransition(None, value),
        force_fresh_employee_session=False,
        now=now,
    )
    conn.execute("COMMIT")


def _employee_events(conn: sqlite3.Connection, ticket_id: str) -> list[dict[str, object]]:
    return [
        json.loads(row["payload"])
        for row in conn.execute(
            "SELECT payload FROM events WHERE entity_id = ? "
            "AND kind = 'employee_session_changed' ORDER BY id",
            (ticket_id,),
        )
    ]


def test_no_employee_session_returns_empty_without_calling_gateway(tmp_path: Path) -> None:
    conn, ticket_id = _ticket_db(tmp_path)

    class NoCallGateway:
        def read_employee_session_history(self, *_args: object) -> EmployeeSessionHistory:
            raise AssertionError("gateway must not be called")

    before = conn.total_changes
    result = read_employee_session_history(conn, NoCallGateway(), ticket_id, 3)  # type: ignore[arg-type]
    assert result == EmployeeSessionHistory(messages=(), employee_session_id=None)
    assert conn.total_changes == before
    assert _employee_events(conn, ticket_id) == []
    conn.close()


def test_rotated_history_id_is_persisted_once_and_repeat_is_idempotent(
    tmp_path: Path,
) -> None:
    conn, ticket_id = _ticket_db(tmp_path)
    _set_employee_session(conn, ticket_id, "old")
    message = EmployeeSessionHistoryMessage("system", "real Hermes context", 7)

    class RotatingGateway:
        calls: list[str] = []

        def read_employee_session_history(
            self, employee_session_id: str, requested_ticket_id: str
        ) -> EmployeeSessionHistory:
            assert requested_ticket_id == ticket_id
            self.calls.append(employee_session_id)
            return EmployeeSessionHistory(
                messages=(message,),
                employee_session_id="new" if employee_session_id == "old" else employee_session_id,
            )

    gateway = RotatingGateway()
    first = read_employee_session_history(conn, gateway, ticket_id, 3)  # type: ignore[arg-type]
    after_first = _employee_events(conn, ticket_id)
    second = read_employee_session_history(conn, gateway, ticket_id, 4)  # type: ignore[arg-type]
    assert first == second == EmployeeSessionHistory((message,), "new")
    assert gateway.calls == ["old", "new"]
    assert (
        after_first
        == _employee_events(conn, ticket_id)
        == [
            {"employee_session_id": "old"},
            {"employee_session_id": "new"},
        ]
    )
    conn.close()


def test_history_redacts_only_the_retired_generated_execution_route_segment(
    tmp_path: Path,
) -> None:
    conn, ticket_id = _ticket_db(tmp_path)
    _set_employee_session(conn, ticket_id, "session")
    generated = EmployeeSessionHistoryMessage(
        "user",
        f"Work ticket {ticket_id} — History ticket. It is at Stage 'needs_success'; "
        "take the next step and propose the 'success' field for approval. "
        "Execution route: hermes_claude. Stage owner: worker.",
        7,
    )
    ordinary = EmployeeSessionHistoryMessage(
        "assistant", "Execution route is ordinary prose here.", 8
    )

    class Gateway:
        def read_employee_session_history(
            self, employee_session_id: str, requested_ticket_id: str
        ) -> EmployeeSessionHistory:
            assert employee_session_id == "session"
            assert requested_ticket_id == ticket_id
            return EmployeeSessionHistory((generated, ordinary), "session")

    result = read_employee_session_history(conn, Gateway(), ticket_id, 9)  # type: ignore[arg-type]

    assert "execution route" not in result.messages[0].text.lower()
    assert result.messages[0].text.endswith("Stage owner: worker.")
    assert result.messages[0].role == "user"
    assert result.messages[0].created_at == 7
    assert result.messages[1] == ordinary
    conn.close()


def test_stale_history_result_is_discarded_and_reread_from_concurrent_winner(
    tmp_path: Path,
) -> None:
    conn, ticket_id = _ticket_db(tmp_path)
    _set_employee_session(conn, ticket_id, "old")
    stale = EmployeeSessionHistoryMessage("assistant", "stale result", 8)
    current = EmployeeSessionHistoryMessage("assistant", "winner result", 9)

    class RacingGateway:
        calls: list[str] = []

        def read_employee_session_history(
            self, employee_session_id: str, requested_ticket_id: str
        ) -> EmployeeSessionHistory:
            assert requested_ticket_id == ticket_id
            self.calls.append(employee_session_id)
            if employee_session_id == "old":
                conn.execute("BEGIN IMMEDIATE")
                tickets_data.write_employee_session_id_in_transaction(
                    conn,
                    ticket_id,
                    transition=EmployeeSessionIdTransition("old", "winner"),
                    force_fresh_employee_session=False,
                    now=3,
                )
                conn.execute("COMMIT")
                return EmployeeSessionHistory((stale,), "loser-rotation")
            return EmployeeSessionHistory((current,), "winner")

    gateway = RacingGateway()
    result = read_employee_session_history(conn, gateway, ticket_id, 4)  # type: ignore[arg-type]
    assert result == EmployeeSessionHistory((current,), "winner")
    assert gateway.calls == ["old", "winner"]
    assert tickets_data.read_ticket(conn, ticket_id).employee_session_id == "winner"
    assert _employee_events(conn, ticket_id) == [
        {"employee_session_id": "old"},
        {"employee_session_id": "winner"},
    ]
    conn.close()


def test_gateway_failure_does_not_write(tmp_path: Path) -> None:
    conn, ticket_id = _ticket_db(tmp_path)
    _set_employee_session(conn, ticket_id, "stored")

    class FailedGateway:
        def read_employee_session_history(
            self, employee_session_id: str, requested_ticket_id: str
        ) -> EmployeeSessionHistory:
            raise RuntimeError("transport broke")

    before = _employee_events(conn, ticket_id)
    with pytest.raises(PlannerError) as caught:
        read_employee_session_history(conn, FailedGateway(), ticket_id, 5)  # type: ignore[arg-type]
    assert caught.value.code is ErrorCode.gateway_offline
    assert _employee_events(conn, ticket_id) == before
    assert tickets_data.read_ticket(conn, ticket_id).employee_session_id == "stored"
    conn.close()
