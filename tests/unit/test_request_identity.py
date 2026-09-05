from __future__ import annotations

import sqlite3

import pytest
from starlette.requests import Request

from planner.core import authctx
from planner.core.errors import ErrorCode, PlannerError


def test_request_identity_classifies_unattributed_chief_and_worker() -> None:
    unattributed = authctx._classify(None)
    chief = authctx._classify("chief")
    worker = authctx._classify("worker", " t_planning ")

    assert (unattributed.actor, unattributed.is_attributed, unattributed.is_chief) == (
        "unattributed",
        False,
        False,
    )
    assert (chief.actor, chief.is_attributed, chief.is_chief) == ("chief", True, True)
    assert (worker.actor, worker.is_attributed, worker.is_chief) == ("worker", True, False)
    assert worker.ticket_id == "t_planning"


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

    assert (local.actor, local.ticket_id) == ("worker", "t_spoofed")
    assert (trusted.actor, trusted.ticket_id) == ("unattributed", None)


def test_direct_write_accepts_unattributed_and_chief_but_rejects_other_actors() -> None:
    authctx.require_direct_write(authctx._classify(None))
    authctx.require_direct_write(authctx._classify("chief"))

    with pytest.raises(PlannerError) as raised:
        authctx.require_direct_write(authctx._classify("worker"))
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


@pytest.mark.parametrize("ticket_id", ("t_coding", "t_sprint"))
def test_ticket_worker_write_accepts_any_stored_ticket_claim(ticket_id: str) -> None:
    conn = _ticket_claim_connection()
    try:
        authctx.require_ticket_worker_write(
            conn,
            authctx._classify("worker", ticket_id),
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("actor", "ticket_id"),
    (
        ("worker", None),
        ("worker", "t_missing"),
        ("agent", "t_coding"),
    ),
)
def test_ticket_worker_write_fails_closed_for_invalid_claims(
    actor: str,
    ticket_id: str | None,
) -> None:
    conn = _ticket_claim_connection()
    try:
        with pytest.raises(PlannerError) as raised:
            authctx.require_ticket_worker_write(
                conn,
                authctx._classify(actor, ticket_id),
            )
    finally:
        conn.close()

    assert raised.value.code is ErrorCode.agent_forbidden
    assert raised.value.detail == {"actor": actor}


@pytest.mark.parametrize(
    ("ticket_id", "capability"),
    (
        ("t_day", "planning-day"),
        ("t_midday", "planning-midday-check"),
        ("t_sprint", "planning-sprint"),
    ),
)
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
    (
        ("worker", None),
        ("worker", "t_missing"),
        ("worker", "t_coding"),
        ("worker", "t_unknown_type"),
        ("worker", "t_sprint"),
        ("agent", "t_day"),
    ),
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


