"""t_tt04a Seam 3 — the approval digest resolves each row's gating field by type.

A coding proposal surfaces on its coding gating field (``kind`` unchanged); a probe
proposal parked at ``needs_alpha`` surfaces on the registry-selected ``alpha`` field.
Rows go in through the real create_ticket / proposal doors; this module carries its
OWN local ``probe_registry`` fixture (Codex F3).
"""

from __future__ import annotations

from collections.abc import Iterator
from sqlite3 import Connection

import pytest
from tests.support.probe import (
    FIELD_ALPHA,
    NEEDS_ALPHA,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.days import data as days_data
from planner.sprints import views as sprints_views
from planner.tickets.contracts import AtCap
from planner.tickets.data import accept_proposal, create_ticket, file_proposal
from planner.tickets.views import queues_view
from planner.worker_types.contracts import WorkerTypeDefinition

TODAY_DAY_ID = "day_2026-07-04"
YESTERDAY_DAY_ID = "day_2026-07-03"


@pytest.fixture
def probe_registry() -> Iterator[WorkerTypeDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


def _approvals(conn: Connection) -> list[dict]:
    view = queues_view(
        conn,
        20,
        "2026-07-04",
        sprints_views.approval_item_rows(conn),
        sprints_views.overdue_item_rows(conn),
        day_id=TODAY_DAY_ID,
    )
    return list(view["approvals"])


def test_approval_digest_coding_kind_unchanged(tmp_db: Connection) -> None:
    ticket = create_ticket(
        tmp_db, worker_type="coding", title="Coding", actor="human", now=1, title_max_chars=200
    )
    # Accept kickoff (default ceiling is now needs_kickoff, so kickoff parks until
    # accepted), expanding the ceiling to needs_success; a success proposal then parks.
    file_proposal(tmp_db, ticket.id, field="kickoff", body="k", actor="agent", now=2)
    accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling="needs_success",
        at_cap=AtCap.propose,
    )
    file_proposal(tmp_db, ticket.id, field="success", body="s", actor="agent", now=3)
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, ticket.id, 3)

    approvals = _approvals(tmp_db)
    assert len(approvals) == 1
    assert approvals[0]["entity_id"] == ticket.id
    assert approvals[0]["kind"] == "success"
    assert approvals[0]["waiting_since"] == 3


def test_approval_digest_probe_surfaces_on_registry_field(
    tmp_db: Connection, probe_registry: WorkerTypeDefinition
) -> None:
    probe = create_ticket(
        tmp_db,
        title="Probe",
        actor="human",
        now=1,
        title_max_chars=200,
        worker_type="probe",
    )
    # Accept kickoff but hold the ceiling at needs_alpha, so the alpha proposal PARKS
    # (state == ceiling) rather than auto-accepting.
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
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, probe.id, 3)

    approvals = _approvals(tmp_db)
    assert len(approvals) == 1
    assert approvals[0]["entity_id"] == probe.id
    assert approvals[0]["kind"] == "alpha"
    assert approvals[0]["waiting_since"] == 3


def test_ticket_approvals_are_limited_to_today_and_appear_when_added(
    tmp_db: Connection,
) -> None:
    on_day = create_ticket(
        tmp_db, worker_type="coding", title="On today", actor="human", now=1, title_max_chars=200
    )
    off_day = create_ticket(
        tmp_db, worker_type="coding", title="Off today", actor="human", now=2, title_max_chars=200
    )
    for ticket, now in ((on_day, 3), (off_day, 4)):
        accept_proposal(
            tmp_db,
            ticket.id,
            field="kickoff",
            actor="human",
            now=now,
            next_ceiling="needs_success",
            at_cap=AtCap.propose,
        )
        file_proposal(
            tmp_db,
            ticket.id,
            field="success",
            body=f"{ticket.title} proposal",
            actor="agent",
            now=now,
        )
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, on_day.id, 5)
    days_data.add_day_ticket(tmp_db, YESTERDAY_DAY_ID, off_day.id, 5)

    assert [entry["entity_id"] for entry in _approvals(tmp_db)] == [on_day.id]

    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, off_day.id, 6)

    assert [entry["entity_id"] for entry in _approvals(tmp_db)] == [on_day.id, off_day.id]
