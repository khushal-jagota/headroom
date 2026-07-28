"""Generic exact-time scheduled Ticket creation, from cadence rule to handoff."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.core import change_signal
from planner.core.clock import TestClock as MutableClock
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import Priority
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.scheduled_tickets import actions, data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketTemplate,
)
from planner.scheduled_tickets.logic import cadence_qualifies, validate_local_time
from planner.scheduled_tickets.runtime import ScheduledTicketLoop
from planner.sprints.logic import DateRange
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


def _now(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone()


def _template(
    *,
    title: str = "Planned session",
    worker_type: str = "coding",
    blocked_by: tuple[str, ...] = (),
) -> ScheduledTicketTemplate:
    return ScheduledTicketTemplate(
        title=title,
        worker_type=worker_type,
        kickoff_note="Gather evidence first.",
        priority=Priority.P3,
        deadline=None,
        project_id=None,
        sprint_id=None,
        sprint_item_id=None,
        employee_backend=None,
        employee_launch_model=None,
        blocked_by_ticket_ids=blocked_by,
    )


def _schedule(
    conn: Connection,
    *,
    local_time: str = "14:30",
    cadence: ScheduleCadence = ScheduleCadence.every_planning_day,
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


def test_exact_time_and_cadence_rules_are_planning_neutral() -> None:
    assert validate_local_time("00:00") == "00:00"
    assert validate_local_time("23:59") == "23:59"
    with pytest.raises(PlannerError) as invalid:
        validate_local_time("7:30")
    assert invalid.value.code is ErrorCode.validation

    ranges = (DateRange("sp_current", "2026-07-20", "2026-08-02"),)
    assert cadence_qualifies(
        ScheduleCadence.every_planning_day,
        planning_day="2026-07-28",
        sprint_ranges=ranges,
    )
    assert not cadence_qualifies(
        ScheduleCadence.current_sprint_final_day,
        planning_day="2026-07-28",
        sprint_ranges=ranges,
    )
    assert cadence_qualifies(
        ScheduleCadence.current_sprint_final_day,
        planning_day="2026-08-02",
        sprint_ranges=ranges,
    )


def test_due_occurrence_creates_and_places_one_ordinary_ticket(tmp_db: Connection) -> None:
    schedule_id = _schedule(tmp_db)
    now = _now("2026-07-28T14:30:20")

    first = actions.run_current_slot(
        tmp_db,
        planning_now=now,
        now=int(now.timestamp()),
        boundary_hour=5,
    )
    repeated = actions.run_current_slot(
        tmp_db,
        planning_now=now.replace(second=59),
        now=int(now.timestamp()) + 39,
        boundary_hour=5,
    )

    assert [item.outcome for item in first] == [OccurrenceOutcome.created]
    assert repeated == first
    ticket_id = first[0].ticket_id
    assert ticket_id is not None
    ticket = tickets_data.read_ticket(tmp_db, ticket_id)
    assert ticket.title == "Planned session"
    assert ticket.worker_type == "coding"
    assert tmp_db.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = 'day_2026-07-28' AND ticket_id = ?",
        (ticket_id,),
    ).fetchone()
    assert len(data.list_occurrences(tmp_db, schedule_id)) == 1


def test_pre_five_am_occurrence_targets_the_previous_planning_day(
    tmp_db: Connection,
) -> None:
    _schedule(tmp_db, local_time="04:30")
    now = _now("2026-07-28T04:30:00")

    result = actions.run_current_slot(
        tmp_db,
        planning_now=now,
        now=int(now.timestamp()),
        boundary_hour=5,
    )

    assert result[0].target_day_id == "day_2026-07-27"
    assert tmp_db.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = 'day_2026-07-27'"
    ).fetchone()


def test_occurrence_commit_emits_the_signal_readiness_already_uses(
    tmp_db: Connection,
) -> None:
    _schedule(tmp_db)
    wakes = 0

    def wake() -> None:
        nonlocal wakes
        wakes += 1

    unsubscribe = change_signal.subscribe(wake)
    try:
        now = _now("2026-07-28T14:30:00")
        actions.run_current_slot(
            tmp_db,
            planning_now=now,
            now=int(now.timestamp()),
            boundary_hour=5,
        )
    finally:
        unsubscribe()

    assert wakes == 1


def test_prelaid_worker_type_suppresses_creation_even_when_ticket_is_dropped(
    tmp_db: Connection,
) -> None:
    now = _now("2026-07-28T14:30:00")
    prelaid = tickets_actions.create_ticket(
        tmp_db,
        title="Manually laid out",
        actor="owner",
        now=int(now.timestamp()) - 60,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        planning_now=now,
        boundary_hour=5,
    )
    tmp_db.execute("UPDATE tickets SET stage = 'dropped' WHERE id = ?", (prelaid.id,))
    _schedule(tmp_db)

    occurrences = actions.run_current_slot(
        tmp_db,
        planning_now=now,
        now=int(now.timestamp()),
        boundary_hour=5,
    )

    assert len(occurrences) == 1
    assert occurrences[0].outcome is OccurrenceOutcome.suppressed
    assert occurrences[0].ticket_id == prelaid.id
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


def test_missed_slot_is_not_scanned_or_backfilled(tmp_db: Connection) -> None:
    schedule_id = _schedule(tmp_db, local_time="14:30")
    late = _now("2026-07-28T14:31:00")

    assert (
        actions.run_current_slot(
            tmp_db,
            planning_now=late,
            now=int(late.timestamp()),
            boundary_hour=5,
        )
        == []
    )
    assert data.list_occurrences(tmp_db, schedule_id) == []
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0


def test_final_sprint_day_uses_canonical_sprint_range(tmp_db: Connection) -> None:
    tmp_db.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, limiting_factor, primary_bet, "
        "supports, premortem, outcomes, solo_reflection, joint_discussion, "
        "updates_to_thinking, carry_forward, created_at, updated_at) "
        "VALUES ('sp_current', 'Current', '2026-07-20', '2026-07-28', '', '', '', '', "
        "'', '', '', '', '', 0, 0)"
    )
    schedule_id = _schedule(
        tmp_db, cadence=ScheduleCadence.current_sprint_final_day
    )
    before = _now("2026-07-27T14:30:00")
    final = _now("2026-07-28T14:30:00")

    assert (
        actions.run_current_slot(
            tmp_db,
            planning_now=before,
            now=int(before.timestamp()),
            boundary_hour=5,
        )
        == []
    )
    result = actions.run_current_slot(
        tmp_db,
        planning_now=final,
        now=int(final.timestamp()),
        boundary_hour=5,
    )
    assert result[0].outcome is OccurrenceOutcome.created
    assert data.list_occurrences(tmp_db, schedule_id) == result


def test_failure_is_recorded_once_and_does_not_stop_another_schedule(
    tmp_db: Connection,
) -> None:
    blocker = tickets_actions.create_ticket(
        tmp_db,
        title="Temporary blocker",
        actor="owner",
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
        planning_now=now,
        now=int(now.timestamp()),
        boundary_hour=5,
    )

    assert sorted(item.outcome.value for item in results) == ["created", "failed"]
    failure = data.list_occurrences(tmp_db, failed_schedule_id)[0]
    assert failure.outcome is OccurrenceOutcome.failed
    assert "from_id must be an existing ticket" in (failure.error or "")
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


def test_api_manages_configuration_and_exposes_occurrences(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduled-api.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_FAKE_NOW": "2026-07-28T14:30:00",
        },
    )
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))

    with TestClient(app) as client:
        created = client.post(
            "/api/schedules",
            json={
                "title": "API schedule",
                "worker_type": "coding",
                "local_time": "14:30",
                "kickoff_note": "From the API",
            },
        )
        assert created.status_code == 200, created.text
        schedule_id = created.json()["id"]
        unknown_type = client.post(
            "/api/schedules",
            json={
                "title": "Bad schedule",
                "worker_type": "not-registered",
                "local_time": "14:30",
            },
        )
        unknown_type_update = client.patch(
            f"/api/schedules/{schedule_id}",
            json={"worker_type": "not-registered"},
        )
        updated = client.patch(
            f"/api/schedules/{schedule_id}",
            json={"cadence": "current_sprint_final_day", "enabled": False},
        )
        shown = client.get(f"/api/schedules/{schedule_id}")
        listed = client.get("/api/schedules")

    assert updated.status_code == 200, updated.text
    assert unknown_type.status_code == 400
    assert unknown_type_update.status_code == 400
    assert updated.json()["enabled"] is False
    assert updated.json()["cadence"] == "current_sprint_final_day"
    assert shown.json()["occurrences"] == []
    assert [item["id"] for item in listed.json()["schedules"]] == [schedule_id]


def test_runtime_loop_uses_injected_clock_and_restart_safe_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduled-loop.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _schedule(conn)
    conn.close()
    clock = MutableClock(_now("2026-07-28T14:30:00"))

    first_loop = ScheduledTicketLoop(str(db_path), clock, boundary_hour=5)
    second_loop = ScheduledTicketLoop(str(db_path), clock, boundary_hour=5)
    first = first_loop.poll_once()
    second = second_loop.poll_once()

    assert first[0].outcome is OccurrenceOutcome.created
    assert second == first
    check = connect(str(db_path))
    try:
        assert check.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
        assert (
            check.execute("SELECT count(*) FROM scheduled_ticket_occurrences").fetchone()[0]
            == 1
        )
    finally:
        check.close()
