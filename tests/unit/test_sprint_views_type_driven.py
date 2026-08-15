"""t_tt04a Seam 4 — item_tickets decodes each child against its OWN type.

A probe child is decoded against PROBE_WORKER_TYPE_DEFINITION (never coding), so it does not throw
on ``needs_alpha`` and its ``has_pending_proposal`` reflects probe's registry gating
field. Children go in through the real create_ticket door; this module carries its OWN
local ``probe_registry`` fixture (Codex F3).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from sqlite3 import Connection

import pytest
from tests.support.probe import (
    FIELD_ALPHA,
    NEEDS_ALPHA,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.core import links as core_links
from planner.core.clock import TestClock
from planner.core.contracts import LinkKind
from planner.sprints.data import create_item
from planner.sprints.views import item_tickets
from planner.tickets.contracts import AtCap
from planner.tickets.data import accept_proposal, create_ticket, file_proposal
from planner.worker_types.contracts import WorkerTypeDefinition


@pytest.fixture
def probe_registry() -> Iterator[WorkerTypeDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


def test_item_tickets_probe_child_decodes(
    tmp_db: Connection, probe_registry: WorkerTypeDefinition
) -> None:
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
    item = create_item(tmp_db, title="Item", project_id="project_vylo", clock=clock)

    probe = create_ticket(
        tmp_db,
        title="Probe child",
        actor="human",
        now=1,
        title_max_chars=200,
        worker_type="probe",
        sprint_item_id=item.id,
    )
    # Hold the ceiling at needs_alpha so the alpha proposal PARKS (stage == ceiling),
    # giving a pending gating proposal on probe's registry-selected field.
    accept_proposal(
        tmp_db,
        probe.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling=NEEDS_ALPHA,
        at_cap=AtCap.propose,
    )
    file_proposal(tmp_db, probe.id, field=FIELD_ALPHA, body="alpha body", actor="agent", now=3)

    rows = item_tickets(tmp_db, item.id)

    assert len(rows) == 1
    row = rows[0]
    # No throw at needs_alpha (a stage coding never has); probe-correct projection.
    assert row["id"] == probe.id
    assert row["stage"] == "needs_alpha"
    assert row["has_pending_proposal"] is True
    assert row["ticket_status"] == "awaiting_approval"
    assert row["review_route"] == "propose"


def test_item_tickets_coding_child_unchanged(tmp_db: Connection) -> None:
    # A coding child projects exactly as before: has_pending_proposal True while a
    # gating proposal is parked at needs_success.
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
    item = create_item(tmp_db, title="Item", project_id="project_vylo", clock=clock)
    child = create_ticket(
        tmp_db,
        worker_type="coding",
        title="Coding child",
        actor="human",
        now=1,
        title_max_chars=200,
        sprint_item_id=item.id,
    )
    # Accept kickoff (default ceiling is now needs_kickoff, so kickoff parks until
    # accepted), expanding the ceiling to needs_success; a success proposal then parks.
    file_proposal(tmp_db, child.id, field="kickoff", body="k", actor="agent", now=2)
    accept_proposal(
        tmp_db,
        child.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling="needs_success",
        at_cap=AtCap.propose,
    )
    file_proposal(tmp_db, child.id, field="success", body="s", actor="agent", now=3)

    rows = item_tickets(tmp_db, item.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == child.id
    assert row["stage"] == "needs_success"
    assert row["has_pending_proposal"] is True


def test_item_tickets_carries_the_two_facts_the_status_group_rule_needs(
    tmp_db: Connection,
) -> None:
    # The Sprint Item page and the workspace rail group Tickets by one rule. It reads
    # the gating field to name a kickoff on its own, and an open blocker link to name
    # Blocked. Neither is visible in the Ticket's own status.
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
    item = create_item(tmp_db, title="Item", project_id="project_vylo", clock=clock)
    child = create_ticket(
        tmp_db,
        worker_type="coding",
        title="Coding child",
        actor="human",
        now=1,
        title_max_chars=200,
        sprint_item_id=item.id,
    )
    blocker = create_ticket(
        tmp_db,
        worker_type="coding",
        title="Blocker",
        actor="human",
        now=1,
        title_max_chars=200,
    )

    row = item_tickets(tmp_db, item.id)[0]
    assert row["gating_field"] == "kickoff"
    assert row["blocked"] is False

    core_links.add_link(tmp_db, blocker.id, child.id, LinkKind.blocks, 2)

    row = item_tickets(tmp_db, item.id)[0]
    assert row["blocked"] is True


@pytest.mark.parametrize(
    ("stage", "ticket_status", "ceiling", "at_cap", "expected"),
    [
        ("needs_closeout", "empty", "needs_closeout", "propose", True),
        ("needs_closeout", "empty", "needs_closeout", "stop", False),
        ("needs_closeout", "empty", "done", "stop", True),
        ("needs_success", "empty", "done", "stop", False),
        ("needs_closeout", "agent", "done", "stop", False),
    ],
)
def test_item_tickets_identifies_only_runnable_empty_closeout_tickets(
    tmp_db: Connection,
    stage: str,
    ticket_status: str,
    ceiling: str,
    at_cap: str,
    expected: bool,
) -> None:
    # Same classification board_view's BoardCard uses (Seam 4 target file, same fact):
    # a ticket is waiting_to_closeout only while its own claim/run can start.
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
    item = create_item(tmp_db, title="Item", project_id="project_vylo", clock=clock)
    child = create_ticket(
        tmp_db,
        worker_type="coding",
        title="Closeout classification",
        actor="human",
        now=1,
        title_max_chars=200,
        sprint_item_id=item.id,
    )
    tmp_db.execute(
        "UPDATE tickets SET stage = ?, ticket_status = ?, ceiling = ?, at_cap = ? "
        "WHERE id = ?",
        (stage, ticket_status, ceiling, at_cap, child.id),
    )

    rows = item_tickets(tmp_db, item.id)

    assert len(rows) == 1
    assert rows[0]["waiting_to_closeout"] is expected
