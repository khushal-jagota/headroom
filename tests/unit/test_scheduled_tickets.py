"""Generic exact-time scheduled Ticket creation, from cadence rule to handoff."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.errors import PlannerError
from planner.core.server import create_app
from planner.scheduled_tickets import actions, data, views
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketTemplate,
)
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale, TicketStatus


def _now(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone()


def _template(
    *,
    title: str = "Planned session",
    worker_type: str = "coding",
    kickoff_note: str = "Gather evidence first.",
    blocked_by: tuple[str, ...] = (),
    project_id: str | None = None,
    manager_id: str | None = None,
) -> ScheduledTicketTemplate:
    return ScheduledTicketTemplate(
        title=title,
        worker_type=worker_type,
        kickoff_note=kickoff_note,
        deadline=None,
        project_id=project_id,
        manager_id=manager_id,
        manager_kind="manager" if manager_id is not None else "chief",
        employee_backend=None,
        employee_launch_model=None,
        blocked_by_ticket_ids=blocked_by,
    )


def _schedule(
    conn: Connection,
    *,
    local_time: str = "14:30",
    cadence: ScheduleCadence = ScheduleCadence.daily,
    template: ScheduledTicketTemplate | None = None,
) -> str:
    created = actions.create_schedule(
        conn,
        enabled=True,
        cadence=cadence,
        local_time=local_time,
        template=template or _template(),
        now=100,
    )
    return created.id


# The six retired configurations retain their explicit Chief assignment. The override
# below tests their destination without reactivating any production routine.
@pytest.mark.parametrize(
    ("title", "local_time", "worker_type", "cadence", "project_id", "deadline"),
    (
        (
            "Plan the day",
            "05:01",
            "planning-day",
            ScheduleCadence.every_planning_day,
            "project_personal",
            "2026-08-03",
        ),
        (
            "Midday check",
            "15:15",
            "planning-midday-check",
            ScheduleCadence.every_planning_day,
            "project_personal",
            None,
        ),
        (
            "Review current sprint and plan the next",
            "17:00",
            "planning-sprint",
            ScheduleCadence.current_sprint_final_day,
            "project_personal",
            None,
        ),
        ("Checkpoint", "17:00", "personal", ScheduleCadence.current_sprint_day_four, None, None),
        ("Knee exercises", "11:00", "personal", ScheduleCadence.daily, "project_personal", None),
        ("20-minute walk", "13:00", "personal", ScheduleCadence.daily, "project_personal", None),
    ),
)
def test_due_occurrence_creates_one_ordinary_ticket_without_day_placement(
    tmp_db: Connection,
    title: str,
    local_time: str,
    worker_type: str,
    cadence: ScheduleCadence,
    project_id: str | None,
    deadline: str | None,
) -> None:
    original = data.create_schedule(
        tmp_db,
        enabled=False,
        cadence=cadence,
        local_time=local_time,
        now=100,
        template=replace(
            _template(title=title, worker_type=worker_type, project_id=project_id),
            deadline=deadline,
            locale=TicketLocale.vps,
            employee_backend="claude",
            employee_launch_model="sonnet",
        ),
    )
    now = _now(f"2026-07-28T{local_time}:20")
    assert actions.run_current_slot(tmp_db, local_now=now, now=int(now.timestamp())) == []
    assert data.list_occurrences(tmp_db, original.id) == []

    # Only the isolated supply fixture receives the supported activation overrides.
    active = data.create_schedule(
        tmp_db,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time=local_time,
        now=101,
        template=replace(
            original.template,
            worker_type="coding",
        ),
    )
    first = actions.run_current_slot(tmp_db, local_now=now, now=int(now.timestamp()))
    repeated = actions.run_current_slot(
        tmp_db, local_now=now.replace(second=59), now=int(now.timestamp()) + 39
    )

    assert [item.outcome for item in first] == [OccurrenceOutcome.created]
    assert repeated == first
    ticket_id = first[0].ticket_id
    assert ticket_id is not None
    ticket = tickets_data.read_ticket(tmp_db, ticket_id)
    assert ticket.title == title
    assert ticket.worker_type == "coding"
    assert ticket.deadline == deadline
    assert ticket.project_id == (project_id or "project_other")
    assert ticket.locale is TicketLocale.vps
    assert ticket.employee_backend == "claude"
    assert ticket.employee_launch_model == "sonnet"
    assert ticket.ticket_status is TicketStatus.awaiting_approval
    assert ticket.pending_proposal is not None
    assert ticket.pending_proposal.body == original.template.kickoff_note
    assert ticket.manager_kind == "chief"
    assert ticket.manager_id is None
    assert ticket.is_backlogged is False
    assert first[0].target_date == "2026-07-28"
    receipt = views.occurrence_json(first[0])
    assert receipt["target_date"] == "2026-07-28"
    assert "target_day_id" not in receipt
    assert len(data.list_occurrences(tmp_db, active.id)) == 1
    assert data.read_schedule(tmp_db, original.id) == original
    assert data.list_occurrences(tmp_db, original.id) == []


def test_failure_is_recorded_once_and_does_not_stop_another_schedule(
    tmp_db: Connection,
) -> None:
    blocker = tickets_actions.create_ticket(
        tmp_db,
        title="Temporary blocker",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
    )
    failed_schedule_id = _schedule(
        tmp_db,
        template=_template(title="Will fail", blocked_by=(blocker.id,)),
    )
    _schedule(
        tmp_db,
        template=_template(title="Still runs", worker_type="exploration"),
    )
    tmp_db.execute("DELETE FROM tickets WHERE id = ?", (blocker.id,))
    now = _now("2026-07-28T14:30:00")

    results = actions.run_current_slot(
        tmp_db,
        local_now=now,
        now=int(now.timestamp()),
    )

    assert sorted(item.outcome.value for item in results) == ["created", "failed"]
    failure = data.list_occurrences(tmp_db, failed_schedule_id)[0]
    assert failure.outcome is OccurrenceOutcome.failed
    assert "blocking_ticket_id must be an existing ticket" in (failure.error or "")
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("worker_type", "cadence"),
    (("personal", ScheduleCadence.daily), ("coding", ScheduleCadence.current_sprint_day_four)),
)
def test_retired_schedule_configuration_cannot_be_rewritten_then_enabled(
    tmp_db: Connection,
    worker_type: str,
    cadence: ScheduleCadence,
) -> None:
    original = data.create_schedule(
        tmp_db,
        enabled=False,
        cadence=cadence,
        local_time="04:30",
        template=_template(worker_type=worker_type),
        now=1,
    )
    rewrites: tuple[dict[str, object], ...] = (
        {"enabled": True},
        {"title": "Replacement"},
        {"worker_type": "coding", "cadence": "daily"},
        {"worker_type": "coding", "cadence": "daily", "enabled": False},
        {"worker_type": "coding", "cadence": "daily", "enabled": True},
    )
    for changes in rewrites:
        with pytest.raises(PlannerError, match="retired"):
            actions.update_schedule(tmp_db, original.id, changes, now=2)
        assert data.read_schedule(tmp_db, original.id) == original

    # Even an already-enabled historical row cannot supply a Ticket. This includes a
    # supported Worker with a retired cadence, independently of retired Worker guards.
    data.update_schedule(tmp_db, original.id, {"enabled": True}, now=3)
    now = _now("2026-07-28T04:30:20")
    assert actions.run_current_slot(tmp_db, local_now=now, now=int(now.timestamp())) == []
    assert data.list_occurrences(tmp_db, original.id) == []
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
    disabled_again = actions.update_schedule(tmp_db, original.id, {"enabled": False}, now=4)
    assert disabled_again.enabled is False
    assert disabled_again.template == original.template
    assert disabled_again.cadence == original.cadence
    original = disabled_again

    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(database)})
    with TestClient(
        create_app(config, build_clock(config), lambda: connect(str(database)))
    ) as client:
        for changes in rewrites:
            response = client.patch(f"/api/schedules/{original.id}", json=changes)
            assert response.status_code == 400, response.text
            assert "retired" in response.json()["error"]["message"]
            assert data.read_schedule(tmp_db, original.id) == original
        disabled = client.patch(f"/api/schedules/{original.id}", json={"enabled": False})
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["enabled"] is False
        assert client.delete(f"/api/schedules/{original.id}").status_code == 405
