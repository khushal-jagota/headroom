from __future__ import annotations

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


def test_cli_headers_say_what_the_environment_says_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One position per process, whichever command was typed.

    A command used to choose between claiming the worker identity and passing the
    ambient one through, so the same process was a Ticket at one verb and Khushal at
    the next. A Ticket id with no actor beside it is not a claim to be that Ticket.
    """
    monkeypatch.setenv("PLAN_ACTOR", "worker")
    monkeypatch.setenv("PLAN_TICKET_ID", "t_worker")
    assert http._headers() == {
        "X-Plan-Actor": "worker",
        "X-Plan-Ticket-ID": "t_worker",
    }

    monkeypatch.delenv("PLAN_ACTOR")
    assert http._headers() == {}


# require_direct_write and require_planning_write used to be pinned here. Both are gone:
# who may act is one question now, asked in planner.core.authority and proved by
# test_authority_rule and the authority matrix. This file keeps its own subject, which is
# turning headers into a truthful Principal.


@pytest.mark.parametrize("actor", ["worker", "sprint_item_supervisor", "agent"])
def test_incomplete_or_unknown_request_identity_fails_closed(actor: str) -> None:
    with pytest.raises(PlannerError) as raised:
        authctx._classify(actor)

    assert raised.value.code is ErrorCode.agent_forbidden
    assert raised.value.detail == {"actor": actor}
