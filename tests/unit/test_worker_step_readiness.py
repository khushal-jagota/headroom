"""The whole worker-step readiness decision, one condition at a time."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal
from tests.support.ticket_progress import advance_ticket

from planner.core.contracts import LinkKind, Priority
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.projects import data as projects_data
from planner.runtime.worker_step_readiness import (
    is_ready_for_worker_step,
    worker_step_blocker,
)
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket, TicketStatus
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION

_FIXED_NOW = datetime(2026, 7, 14, 12, 0, 0).astimezone()
PLANNING_DAY_ID = "day_2026-07-14"
OTHER_DAY_ID = "day_2026-07-13"


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "readiness.db"))
    create_schema(conn)
    return conn


def _ticket(
    conn: sqlite3.Connection,
    *,
    worker_type: str = "coding",
    planning_day_id: str | None = PLANNING_DAY_ID,
    ceiling: str | None = None,
    project_id: str | None = None,
    sprint_item_id: str | None = None,
) -> Ticket:
    definition = configured_worker_type_registry().require(worker_type)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type=worker_type,
        title=f"{worker_type} ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        project_id=project_id,
        sprint_item_id=sprint_item_id,
    )
    ticket = tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        principal=OWNER_PRINCIPAL,
        now=2,
        next_ceiling=ceiling or definition.first_worker_stage(),
        next_holder=OWNER_PRINCIPAL,
    )
    if planning_day_id is not None:
        days_data.add_day_ticket(conn, planning_day_id, ticket.id, 3)
    return ticket


def _ready(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str = PLANNING_DAY_ID,
    definition: WorkerTypeDefinition | None = None,
) -> bool:
    return is_ready_for_worker_step(
        conn,
        tickets_data.read_ticket(conn, ticket.id),
        planning_day_id=planning_day_id,
        worker_type_definition=(
            definition or configured_worker_type_registry().require(ticket.worker_type)
        ),
    )


def _blocker(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str = PLANNING_DAY_ID,
    definition: WorkerTypeDefinition | None = None,
) -> str | None:
    return worker_step_blocker(
        conn,
        tickets_data.read_ticket(conn, ticket.id),
        planning_day_id=planning_day_id,
        worker_type_definition=(
            definition or configured_worker_type_registry().require(ticket.worker_type)
        ),
    )


def _block(conn: sqlite3.Connection, *, blocker_id: str, target_id: str, now: int) -> None:
    """Block a Ticket the way the API does, so its status settles to `blocked`."""
    tickets_actions.add_link(conn, blocker_id, target_id, LinkKind.blocks, now=now)


@pytest.mark.parametrize(
    ("worker_type", "first_stage", "expected_ready", "definition"),
    [
        ("coding", "needs_success", True, CODING_WORKER_TYPE_DEFINITION),
        ("new_worker", "needs_understanding", True, NEW_WORKER_TYPE_DEFINITION),
    ],
)
def test_shipped_worker_types_use_their_real_first_worker_stage_ownership(
    tmp_path: Path,
    worker_type: str,
    first_stage: str,
    expected_ready: bool,
    definition: WorkerTypeDefinition,
) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, worker_type=worker_type)
        assert ticket.stage == first_stage
        assert _ready(conn, ticket, definition=definition) is expected_ready
    finally:
        conn.close()


def test_membership_must_match_the_explicit_planning_day(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        no_day = _ticket(conn, planning_day_id=None)
        other_day = _ticket(conn, planning_day_id=OTHER_DAY_ID)
        supplied_day = _ticket(conn)

        assert not _ready(conn, no_day)
        assert not _ready(conn, other_day)
        assert _ready(conn, supplied_day)
        assert not _ready(conn, supplied_day, planning_day_id=OTHER_DAY_ID)
    finally:
        conn.close()


def test_user_owned_ticket_is_ready_once_per_stage_entry(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, worker_type="new_worker")
        assert _ready(conn, ticket, definition=NEW_WORKER_TYPE_DEFINITION)
        conn.execute(
            "INSERT INTO ticket_paired_stage_openers(ticket_id, stage, opened_at) "
            "VALUES (?, ?, 4)",
            (ticket.id, ticket.stage),
        )
        assert not _ready(conn, ticket, definition=NEW_WORKER_TYPE_DEFINITION)

        moved = advance_ticket(
            conn,
            ticket.id,
            new_stage="needs_stages",
            principal=OWNER_PRINCIPAL,
            now=5,
        )
        assert conn.execute(
            "SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id = ?",
            (ticket.id,),
        ).fetchone() is None
        assert _ready(conn, moved, definition=NEW_WORKER_TYPE_DEFINITION)
    finally:
        conn.close()


# Only `empty` is startable; every other control status is a deliberate "not ready".
# This partitions the full TicketStatus set so a future status cannot silently become
# auto-startable.
_READY_UNDER_WORKER_OWNERSHIP: dict[TicketStatus, bool] = {
    TicketStatus.empty: True,
    TicketStatus.blocked: False,
    TicketStatus.agent: False,
    TicketStatus.awaiting_approval: False,
    TicketStatus.errored: False,
}


@pytest.mark.parametrize(
    ("worker_type", "ceiling"),
    [
        ("coding", "needs_approach"),
        ("coding", "needs_success"),
        ("new_worker", "needs_thinking"),
        ("new_worker", "needs_stages"),
    ],
)
def test_a_ticket_at_its_ceiling_is_still_started_to_propose(
    tmp_path: Path,
    worker_type: str,
    ceiling: str,
) -> None:
    """A ceiling names the last thing a worker does, so it still takes that step."""
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, worker_type=worker_type, ceiling=ceiling)
        if worker_type == "new_worker":
            tickets_data.file_current_proposal(
                conn,
                ticket.id,
                body="understanding",
                principal=ticket_principal(ticket.id),
                now=3,
            )
            ticket = tickets_data.accept_proposal(
                conn,
                ticket.id,
                field="understanding",
                principal=OWNER_PRINCIPAL,
                now=4,
                next_ceiling=ceiling,
                next_holder=OWNER_PRINCIPAL,
            )
            assert ticket.stage == "needs_stages"
        assert _ready(conn, ticket) is True
    finally:
        conn.close()


@pytest.mark.parametrize("settled_stage", ["done", "dropped"])
def test_a_completing_blocker_frees_its_target(tmp_path: Path, settled_stage: str) -> None:
    # Readiness itself does not look at links: the completing blocker rewrites the
    # target's status back to empty, and that is what makes it startable again.
    conn = _db(tmp_path)
    try:
        blocker = _ticket(conn, planning_day_id=None)
        target = _ticket(conn)
        _block(conn, blocker_id=blocker.id, target_id=target.id, now=4)
        assert tickets_data.read_ticket(conn, target.id).ticket_status is TicketStatus.blocked
        assert not _ready(conn, target)

        if settled_stage == "done":
            advance_ticket(conn, blocker.id, new_stage="done", principal=OWNER_PRINCIPAL, now=5)
        else:
            tickets_data.drop_ticket(conn, blocker.id, principal=OWNER_PRINCIPAL, now=5)

        assert tickets_data.read_ticket(conn, target.id).ticket_status is TicketStatus.empty
        assert _ready(conn, target)
    finally:
        conn.close()


_EXPECTED_BLOCKERS = {
    "membership": "the Ticket is not on today's Day",
    "status": "the Ticket is at user, so no worker step is due",
    "terminal": "the Stage done is terminal",
    "next_gate": "the Stage needs_success has no field for a worker to fill",
    # Filing a proposal parks it and writes `awaiting_approval` in the same breath, and
    # the status is asked about first. The Ticket is refused either way.
    "proposal": "the Ticket is at awaiting_approval, so no worker step is due",
    "scope": "the Ticket is at its ceiling and the cap is stop",
    "blocker": "the Ticket is at blocked, so no worker step is due",
}


def test_a_ready_ticket_names_no_blocker(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        ticket = _ticket(conn, ceiling="needs_success")
        assert _blocker(conn, ticket) is None
        assert _ready(conn, ticket)
    finally:
        conn.close()


# --- the Closeout lane ---------------------------------------------------------


def _to_closeout(conn: sqlite3.Connection, ticket_id: str) -> None:
    advance_ticket(conn, ticket_id, new_stage="needs_closeout", principal=OWNER_PRINCIPAL, now=5)


@pytest.mark.parametrize(
    "occupying_status",
    [
        TicketStatus.agent,
        TicketStatus.awaiting_approval,
        TicketStatus.errored,
    ],
)
def test_a_closeout_waiter_is_not_ready_while_its_lane_is_occupied(
    tmp_path: Path, occupying_status: TicketStatus
) -> None:
    conn = _db(tmp_path)
    try:
        occupying = _ticket(conn, planning_day_id=None)
        waiting = _ticket(conn)
        _to_closeout(conn, occupying.id)
        _to_closeout(conn, waiting.id)
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (occupying_status.value, occupying.id),
        )
        assert not _ready(conn, waiting)
    finally:
        conn.close()


def test_lanes_in_different_projects_or_worker_types_are_independent(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    try:
        other_project_id = projects_data.create_project(
            conn, name="Client Work", priority=Priority.P2, now=0
        ).id
        occupying = _ticket(conn, planning_day_id=None, project_id=other_project_id)
        other_project = _ticket(conn)
        _to_closeout(conn, occupying.id)
        _to_closeout(conn, other_project.id)
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (TicketStatus.agent.value, occupying.id),
        )
        assert _ready(conn, other_project)
    finally:
        conn.close()
