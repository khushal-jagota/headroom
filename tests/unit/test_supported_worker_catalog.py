"""New work uses the supported catalog; historical work keeps its definitions."""

from __future__ import annotations

from datetime import datetime
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.retired_workers import seed_historical_worker_type
from tests.unit.test_scheduled_tickets import _template

from planner.core.errors import PlannerError
from planner.scheduled_tickets import actions as schedule_actions
from planner.scheduled_tickets import data as schedule_data
from planner.scheduled_tickets.contracts import ScheduleCadence
from planner.tickets import actions, data
from planner.worker_types.contracts import RETIRED_WORKER_TYPES


def _install_history(conn: Connection) -> None:
    for worker_type in sorted(RETIRED_WORKER_TYPES):
        seed_historical_worker_type(conn, worker_type)


@pytest.mark.parametrize("worker_type", sorted(RETIRED_WORKER_TYPES))
def test_retired_types_are_refused_by_all_creation_and_schedule_writers(
    tmp_db: Connection,
    worker_type: str,
) -> None:
    for create in (
        lambda: data.create_ticket(
            tmp_db,
            title="Retired direct writer",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type=worker_type,
        ),
        lambda: actions.create_ticket(
            tmp_db,
            title="Retired",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type=worker_type,
        ),
        lambda: data.validate_ticket_creation_context(
            tmp_db,
            title="Retired",
            title_max_chars=200,
            worker_type=worker_type,
        ),
        lambda: schedule_actions.create_schedule(
            tmp_db,
            principal=OWNER_PRINCIPAL,
            enabled=False,
            cadence=ScheduleCadence.daily,
            local_time="14:30",
            template=_template(worker_type=worker_type),
            now=1,
        ),
    ):
        with pytest.raises(PlannerError, match="retired"):
            create()
    saved = schedule_data.create_schedule(
        tmp_db,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=_template(worker_type=worker_type),
        now=1,
    )
    for changes in ({"enabled": True}, {"title": "Changed"}):
        with pytest.raises(PlannerError, match="retired"):
            schedule_actions.update_schedule(
                tmp_db, saved.id, changes, principal=OWNER_PRINCIPAL, now=2
            )
    assert (
        schedule_actions.run_current_slot(
            tmp_db,
            local_now=datetime.fromisoformat("2026-10-16T14:30:00").astimezone(),
            now=3,
        )
        == []
    )
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    assert (
        schedule_actions.update_schedule(
            tmp_db,
            saved.id,
            {"enabled": False},
            principal=OWNER_PRINCIPAL,
            now=4,
        ).enabled
        is False
    )
    supported = schedule_data.create_schedule(
        tmp_db,
        enabled=False,
        cadence=ScheduleCadence.daily,
        local_time="14:30",
        template=_template(),
        now=1,
    )
    with pytest.raises(PlannerError, match="retired"):
        schedule_actions.update_schedule(
            tmp_db,
            supported.id,
            {"worker_type": worker_type},
            principal=OWNER_PRINCIPAL,
            now=5,
        )
