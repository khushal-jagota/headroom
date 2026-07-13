"""t_tt04a Seam 4 — item_tickets decodes each child against its OWN type.

A probe child is decoded against PROBE_DEFINITION (never coding), so it does not throw
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

from planner.core.clock import TestClock
from planner.sprints.data import create_item
from planner.sprints.views import item_tickets
from planner.ticket_types.contracts import WorkflowDefinition
from planner.tickets.contracts import AtCap, FieldName
from planner.tickets.data import accept_proposal, create_ticket, file_proposal


@pytest.fixture
def probe_registry() -> Iterator[WorkflowDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


def test_item_tickets_probe_child_decodes(
    tmp_db: Connection, probe_registry: WorkflowDefinition
) -> None:
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
    item = create_item(tmp_db, title="Item", project_id="project_vylo", clock=clock)

    probe = create_ticket(
        tmp_db, title="Probe child", actor="human", now=1, title_max_chars=200,
        ticket_type="probe", sprint_item_id=item.id,
    )
    # Hold the ceiling at needs_alpha so the alpha proposal PARKS (state == ceiling),
    # giving a pending gating proposal on probe's registry-selected field.
    accept_proposal(
        tmp_db, probe.id, field=FieldName.kickoff, actor="human", now=2,
        next_ceiling=NEEDS_ALPHA, at_cap=AtCap.propose,
    )
    file_proposal(tmp_db, probe.id, field=FIELD_ALPHA, body="alpha body", actor="agent", now=3)

    rows = item_tickets(tmp_db, item.id)

    assert len(rows) == 1
    row = rows[0]
    # No throw at needs_alpha (a state coding never has); probe-correct projection.
    assert row["id"] == probe.id
    assert row["state"] == "needs_alpha"
    assert row["has_pending_proposal"] is True
    assert row["ticket_status"] == "awaiting_approval"


def test_item_tickets_coding_child_unchanged(tmp_db: Connection) -> None:
    # A coding child projects exactly as before: has_pending_proposal True while a
    # gating proposal is parked at needs_success.
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
    item = create_item(tmp_db, title="Item", project_id="project_vylo", clock=clock)
    child = create_ticket(
        tmp_db, title="Coding child", actor="human", now=1, title_max_chars=200,
        sprint_item_id=item.id,
    )
    # Accept kickoff (default ceiling is now needs_kickoff, so kickoff parks until
    # accepted), expanding the ceiling to needs_success; a success proposal then parks.
    file_proposal(tmp_db, child.id, field=FieldName.kickoff, body="k", actor="agent", now=2)
    accept_proposal(
        tmp_db, child.id, field=FieldName.kickoff, actor="human", now=2,
        next_ceiling="needs_success", at_cap=AtCap.propose,
    )
    file_proposal(tmp_db, child.id, field=FieldName.success, body="s", actor="agent", now=3)

    rows = item_tickets(tmp_db, item.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == child.id
    assert row["state"] == "needs_success"
    assert row["has_pending_proposal"] is True
