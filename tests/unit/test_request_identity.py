from __future__ import annotations

import pytest

from planner.cli import http


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


