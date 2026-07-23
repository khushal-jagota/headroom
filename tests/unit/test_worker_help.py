"""Worker-requested human-help lifecycle contracts."""

from __future__ import annotations

from sqlite3 import Connection

import pytest

from planner.core.contracts import ErrorCode, PlannerError
from planner.days import data as days_data
from planner.runtime.automatic_employee_step_eligibility import (
    is_eligible_for_automatic_employee_step,
)
from planner.tickets import data
from planner.tickets.contracts import AtCap, TicketStatus, WorkspaceDotFacts, WorkspaceDotState
from planner.tickets.logic.workspace_dot import workspace_dot_state
from planner.tickets.views import review_view
from planner.worker_types.configuration import configured_worker_type_registry

DAY_ID = "day_2026-07-23"


def _ticket(conn: Connection):
    ticket = data.create_ticket(
        conn,
        worker_type="coding",
        title="Worker help",
        actor="human",
        now=1,
        title_max_chars=200,
        kickoff_note="intake",
    )
    return data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling="needs_success",
        at_cap=AtCap.propose,
    )


def test_fresh_schema_is_marked_at_current_version(tmp_db: Connection) -> None:
    assert int(tmp_db.execute("PRAGMA user_version").fetchone()[0]) == 35


def test_worker_help_pauses_dispatch_and_requires_explicit_release(tmp_db: Connection) -> None:
    ticket = _ticket(tmp_db)
    days_data.add_day_ticket(tmp_db, DAY_ID, ticket.id, 3)
    definition = configured_worker_type_registry().require("coding")

    requested = data.request_user_help(tmp_db, ticket.id, actor="agent", now=4)
    assert requested.ticket_status is TicketStatus.needs_user
    assert not is_eligible_for_automatic_employee_step(
        tmp_db,
        requested,
        planning_day_id=DAY_ID,
        worker_type_definition=definition,
    )
    assert workspace_dot_state(
        WorkspaceDotFacts(ticket_status=TicketStatus.needs_user)
    ) is WorkspaceDotState.needs_attention
    assert review_view(tmp_db, day_id=DAY_ID)["user_help_requests"] == [
        {"ticket_id": ticket.id, "title": "Worker help", "waiting_since": 4}
    ]

    still_waiting = data.release_ticket(tmp_db, ticket.id, now=5)
    assert still_waiting.ticket_status is TicketStatus.empty
    assert review_view(tmp_db, day_id=DAY_ID)["user_help_requests"] == []


@pytest.mark.parametrize("actor", ["unattributed", "chief", "human"])
def test_worker_help_rejects_direct_actors(tmp_db: Connection, actor: str) -> None:
    ticket = _ticket(tmp_db)
    with pytest.raises(PlannerError) as raised:
        data.request_user_help(tmp_db, ticket.id, actor=actor, now=4)
    assert raised.value.code is ErrorCode.agent_forbidden
