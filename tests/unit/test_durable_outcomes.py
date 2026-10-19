"""Historical Sprint partitions stay readable alongside current Ticket classification."""

from __future__ import annotations

from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.ticket_progress import advance_ticket

from planner.core import authority
from planner.core.authctx import _classify
from planner.core.authority import require_above, require_above_or_self
from planner.core.clock import TestClock as Clock
from planner.core.errors import PlannerError
from planner.sprints import data
from planner.tickets import data as tickets
from planner.tickets.contracts import Ticket


def _ticket(conn: Connection, outcome_id: str, sprint_id: str, title: str) -> Ticket:
    return tickets.create_ticket(
        conn,
        title=title,
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        sprint_item_id=outcome_id,
        sprint_id=sprint_id,
    )




def test_ticket_classification_preserves_existing_authority(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    first = data.create_sprint(
        tmp_db, name="First", date_start="2026-07-01", date_end="2026-07-07", clock=fake_clock
    )
    second = data.create_sprint(
        tmp_db, name="Second", date_start="2026-07-08", date_end="2026-07-14", clock=fake_clock
    )
    outcome = data.create_item(tmp_db, title="O", project_id="project_vylo", clock=fake_clock)
    other = data.create_item(tmp_db, title="Other", project_id="project_vylo", clock=fake_clock)
    valid = _ticket(tmp_db, outcome.id, first.id, "Valid")
    stale = _ticket(tmp_db, outcome.id, first.id, "Completed since selection")
    advance_ticket(tmp_db, stale.id, new_stage="done", principal=OWNER_PRINCIPAL, now=2)
    worker = _classify("worker", valid.id)
    # A Ticket classifies itself, and no longer any Ticket that happens to exist.
    stale_worker = _classify("worker", stale.id)
    with pytest.raises(PlannerError):
        tickets.classify_ticket(
            tmp_db,
            stale.id,
            sprint_item_id=other.id,
            principal=worker.principal,
            now=3,
            admit=lambda: require_above_or_self(
                tmp_db, worker.principal, authority.ticket(stale.id)
            ),
        )
    tickets.classify_ticket(
        tmp_db,
        stale.id,
        sprint_item_id=other.id,
        principal=stale_worker.principal,
        now=3,
        admit=lambda: require_above_or_self(
            tmp_db, stale_worker.principal, authority.ticket(stale.id)
        ),
    )
    assert tickets.read_ticket(tmp_db, stale.id).sprint_item_id == other.id
    supervisor = _classify("sprint_item_supervisor", None, outcome.id)
    tickets.edit_ticket(
        tmp_db,
        valid.id,
        edit={"sprint_id": second.id},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    require_above(tmp_db, supervisor.principal, authority.ticket(valid.id))
    tickets.classify_ticket(
        tmp_db, valid.id, sprint_item_id=other.id, principal=OWNER_PRINCIPAL, now=4
    )
    with pytest.raises(PlannerError):
        require_above(tmp_db, supervisor.principal, authority.ticket(valid.id))
