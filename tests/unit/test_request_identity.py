from __future__ import annotations

import sqlite3

import pytest
from starlette.requests import Request

from planner.cli import http
from planner.core import authctx
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError


def test_request_identity_classifies_unattributed_chief_and_worker() -> None:
    unattributed = authctx._classify(None)
    chief = authctx._classify("chief")
    worker = authctx._classify("worker", " t_planning ")
    supervisor = authctx._classify("sprint_item_supervisor", sprint_item_id=" si_one ")

    assert unattributed.principal == OWNER_PRINCIPAL
    assert chief.principal == CHIEF_PRINCIPAL
    assert worker.principal == Principal(PrincipalKind.ticket, "t_planning")
    assert supervisor.principal == Principal(PrincipalKind.sprint_item, "si_one")


def test_request_identity_reads_ticket_header_and_honors_trusted_scope_override() -> None:
    headers = [
        (b"x-plan-actor", b"worker"),
        (b"x-plan-ticket-id", b"t_spoofed"),
    ]
    local = authctx.request_context(Request({"type": "http", "headers": headers}))
    trusted = authctx.request_context(
        Request(
            {
                "type": "http",
                "headers": headers,
                authctx.PLAN_ACTOR_SCOPE_KEY: None,
                authctx.PLAN_TICKET_ID_SCOPE_KEY: None,
            }
        )
    )

    assert local.principal == Principal(PrincipalKind.ticket, "t_spoofed")
    assert trusted.principal == OWNER_PRINCIPAL


def test_worker_cli_headers_name_the_ticket_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PLAN_ACTOR", raising=False)
    monkeypatch.setenv("PLAN_TICKET_ID", "t_worker")

    assert http._headers("worker") == {
        "X-Plan-Actor": "worker",
        "X-Plan-Ticket-ID": "t_worker",
    }


def test_direct_write_accepts_unattributed_and_chief_but_rejects_other_actors() -> None:
    authctx.require_direct_write(authctx._classify(None))
    authctx.require_direct_write(authctx._classify("chief"))

    with pytest.raises(PlannerError) as raised:
        authctx.require_direct_write(authctx._classify("worker", "t_one"))
    assert raised.value.code is ErrorCode.agent_forbidden
    assert raised.value.detail == {"actor": "worker"}


def _ticket_claim_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tickets (id TEXT PRIMARY KEY, worker_type TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO tickets (id, worker_type) VALUES (?, ?)",
        (
            ("t_day", "planning-day"),
            ("t_midday", "planning-midday-check"),
            ("t_sprint", "planning-sprint"),
            ("t_coding", "coding"),
            ("t_unknown_type", "planning-future"),
        ),
    )
    return conn


@pytest.mark.parametrize(("ticket_id", "capability"), (("t_day", "planning-day"),))
def test_planning_write_requires_exact_stored_worker_capability(
    ticket_id: str,
    capability: authctx.PlanningCapability,
) -> None:
    conn = _ticket_claim_connection()
    try:
        authctx.require_planning_write(
            conn,
            authctx._classify("worker", ticket_id),
            capability,
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("actor", "ticket_id"),
    (("worker", "t_sprint"),),
)
def test_planning_write_fails_closed_for_missing_unknown_or_nonmatching_claims(
    actor: str,
    ticket_id: str | None,
) -> None:
    conn = _ticket_claim_connection()
    try:
        with pytest.raises(PlannerError) as raised:
            authctx.require_planning_write(
                conn,
                authctx._classify(actor, ticket_id),
                "planning-day",
            )
    finally:
        conn.close()

    assert raised.value.code is ErrorCode.agent_forbidden
    assert raised.value.detail == {"actor": actor, "capability": "planning-day"}


@pytest.mark.parametrize("actor", ["worker", "sprint_item_supervisor", "agent"])
def test_incomplete_or_unknown_request_identity_fails_closed(actor: str) -> None:
    with pytest.raises(PlannerError) as raised:
        authctx._classify(actor)

    assert raised.value.code is ErrorCode.agent_forbidden
    assert raised.value.detail == {"actor": actor}
