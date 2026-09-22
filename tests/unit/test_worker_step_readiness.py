"""The whole worker-step readiness decision, one condition at a time."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal
from tests.support.probe import shipped_definition
from tests.support.ticket_progress import advance_ticket

from planner.core.contracts import Priority
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.projects import data as projects_data
from planner.runtime.worker_step_readiness import (
    is_ready_for_worker_step,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.contracts import WorkerTypeDefinition

NEW_WORKER_TYPE_DEFINITION = shipped_definition("new_worker")

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
        kickoff_note="Agreed brief.",
        project_id=project_id,
        sprint_item_id=sprint_item_id,
    )
    ticket = tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=2,
        next_ceiling=ceiling or definition.stage_ids()[1],
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


@pytest.mark.parametrize(
    ("worker_type", "ceiling"),
    [
        ("coding", "needs_what_changes"),
        ("coding", "needs_success_condition"),
        ("new_worker", "needs_what_good_looks_like_at_each_stage"),
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
                body="purpose_and_boundaries",
                principal=ticket_principal(ticket.id),
                now=3,
            )
            ticket = tickets_data.accept_proposal(
                conn,
                ticket.id,
                field="purpose_and_boundaries",
                principal=OWNER_PRINCIPAL,
                now=4,
                next_ceiling=ceiling,
                next_holder=OWNER_PRINCIPAL,
            )
            assert ticket.stage == "needs_stages"
        assert _ready(conn, ticket) is True
    finally:
        conn.close()


# --- the Closeout lane ---------------------------------------------------------


def _to_closeout(conn: sqlite3.Connection, ticket_id: str) -> None:
    advance_ticket(
        conn, ticket_id, new_stage="needs_consequences", principal=OWNER_PRINCIPAL, now=5
    )


@pytest.mark.parametrize(
    "occupy",
    [
        "UPDATE tickets SET worker_step_claim = 'out' WHERE id = ?",
        "UPDATE tickets SET pending_proposal = '{}' WHERE id = ?",
        "UPDATE tickets SET worker_step_claim = 'errored' WHERE id = ?",
    ],
    ids=["agent", "awaiting_approval", "errored"],
)
def test_a_closeout_waiter_is_not_ready_while_its_lane_is_occupied(
    tmp_path: Path, occupy: str
) -> None:
    conn = _db(tmp_path)
    try:
        occupying = _ticket(conn, planning_day_id=None)
        waiting = _ticket(conn)
        _to_closeout(conn, occupying.id)
        _to_closeout(conn, waiting.id)
        conn.execute(occupy, (occupying.id,))
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
            "UPDATE tickets SET worker_step_claim = 'out' WHERE id = ?",
            (occupying.id,),
        )
        assert _ready(conn, other_project)
    finally:
        conn.close()
