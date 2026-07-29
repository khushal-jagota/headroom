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
from planner.runtime import worker_step_readiness
from planner.scheduled_tickets import actions, data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketPlacementMode,
    ScheduledTicketTemplate,
)
from planner.scheduled_tickets.logic import cadence_qualifies, validate_local_time
from planner.scheduled_tickets.runtime import ScheduledTicketLoop
from planner.sprints.logic import DateRange
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketStatus
from planner.tickets.logic import fields_codec
from planner.worker_types.configuration import configured_worker_type_registry


def _now(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone()


def _template(
    *,
    title: str = "Planned session",
    worker_type: str = "coding",
    kickoff_note: str = "Gather evidence first.",
    blocked_by: tuple[str, ...] = (),
    project_id: str | None = None,
    placement_mode: ScheduledTicketPlacementMode = ScheduledTicketPlacementMode.current_sprint,
    sprint_item_id: str | None = None,
) -> ScheduledTicketTemplate:
    return ScheduledTicketTemplate(
        title=title,
        worker_type=worker_type,
        kickoff_note=kickoff_note,
        priority=Priority.P3,
        deadline=None,
        project_id=project_id,
        placement_mode=placement_mode,
        sprint_item_id=sprint_item_id,
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


def _production_planning_schedules(
    conn: Connection,
) -> dict[str, str]:
    conn.execute(
        "INSERT OR IGNORE INTO projects "
        "(id, name, summary, created_at, updated_at) "
        "VALUES ('project_panels', 'Panels', '', 0, 0)"
    )
    definitions = (
        (
            "Plan the day",
            "planning-day",
            "05:05",
            ScheduleCadence.every_planning_day,
        ),
        (
            "Check the day at 14:30",
            "planning-midday-check",
            "14:30",
            ScheduleCadence.every_planning_day,
        ),
        (
            "Review current sprint and plan the next",
            "planning-sprint",
            "17:00",
            ScheduleCadence.current_sprint_final_day,
        ),
    )
    return {
        worker_type: _schedule(
            conn,
            local_time=local_time,
            cadence=cadence,
            template=_template(
                title=title,
                worker_type=worker_type,
                kickoff_note="",
                project_id="project_panels",
                placement_mode=ScheduledTicketPlacementMode.current_sprint,
            ),
        )
        for title, worker_type, local_time, cadence in definitions
    }


def _insert_current_sprint(
    conn: Connection, *, date_end: str = "2026-07-28"
) -> None:
    conn.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_current', 'Current', '2026-07-20', ?, 0, 0)",
        (date_end,),
    )


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


def test_approved_production_planning_schedule_definitions_are_exact(
    tmp_db: Connection,
) -> None:
    schedule_ids = _production_planning_schedules(tmp_db)
    schedules = {
        schedule.template.worker_type: schedule
        for schedule in data.list_schedules(tmp_db)
    }

    assert set(schedules) == set(schedule_ids) == {
        "planning-day",
        "planning-midday-check",
        "planning-sprint",
    }
    assert {
        worker_type: (
            schedule.template.title,
            schedule.local_time,
            schedule.cadence,
        )
        for worker_type, schedule in schedules.items()
    } == {
        "planning-day": (
            "Plan the day",
            "05:05",
            ScheduleCadence.every_planning_day,
        ),
        "planning-midday-check": (
            "Check the day at 14:30",
            "14:30",
            ScheduleCadence.every_planning_day,
        ),
        "planning-sprint": (
            "Review current sprint and plan the next",
            "17:00",
            ScheduleCadence.current_sprint_final_day,
        ),
    }
    for schedule in schedules.values():
        assert schedule.enabled
        assert schedule.template.priority is Priority.P3
        assert schedule.template.project_id == "project_panels"
        assert (
            schedule.template.placement_mode
            is ScheduledTicketPlacementMode.current_sprint
        )
        assert schedule.template.kickoff_note == ""
        assert schedule.template.deadline is None
        assert schedule.template.sprint_item_id is None
        assert schedule.template.employee_backend is None
        assert schedule.template.employee_launch_model is None
        assert schedule.template.blocked_by_ticket_ids == ()


def test_production_planning_schedules_create_place_receipt_and_reach_handoff(
    tmp_db: Connection,
) -> None:
    _insert_current_sprint(tmp_db)
    schedule_ids = _production_planning_schedules(tmp_db)
    morning = _now("2026-07-28T05:05:00")
    midday = _now("2026-07-28T14:30:00")
    sprint_end = _now("2026-07-28T17:00:00")

    morning_results = actions.run_current_slot(
        tmp_db,
        planning_now=morning,
        now=int(morning.timestamp()),
        boundary_hour=5,
    )
    repeated_morning = actions.run_current_slot(
        tmp_db,
        planning_now=morning.replace(second=59),
        now=int(morning.timestamp()) + 59,
        boundary_hour=5,
    )
    midday_results = actions.run_current_slot(
        tmp_db,
        planning_now=midday,
        now=int(midday.timestamp()),
        boundary_hour=5,
    )
    sprint_results = actions.run_current_slot(
        tmp_db,
        planning_now=sprint_end,
        now=int(sprint_end.timestamp()),
        boundary_hour=5,
    )

    assert repeated_morning == morning_results
    assert len(morning_results) == 1
    assert len(midday_results) == 1
    assert len(sprint_results) == 1
    results = morning_results + midday_results + sprint_results
    assert {result.schedule_id for result in results} == set(schedule_ids.values())
    assert {result.outcome for result in results} == {OccurrenceOutcome.created}
    assert {result.target_day_id for result in results} == {"day_2026-07-28"}

    registry = configured_worker_type_registry()
    for result in results:
        assert result.ticket_id is not None
        ticket = tickets_data.read_ticket(tmp_db, result.ticket_id)
        assert ticket.worker_type in schedule_ids
        assert ticket.priority is Priority.P3
        assert ticket.deadline is None
        assert ticket.ticket_status is TicketStatus.empty
        assert fields_codec.get_slot(ticket.fields, "kickoff").proposal is None
        assert ticket.project_id == "project_panels"
        assert ticket.effective_sprint_id == "sp_current"
        assert ticket.sprint_item_id is not None
        assert (
            tmp_db.execute(
                "SELECT kind FROM sprint_items WHERE id = ?",
                (ticket.sprint_item_id,),
            ).fetchone()["kind"]
            == "other"
        )
        assert data.list_occurrences(tmp_db, result.schedule_id) == [result]

        assert worker_step_readiness.is_ready_for_worker_step(
            tmp_db,
            ticket,
            planning_day_id="day_2026-07-28",
            worker_type_definition=registry.require(ticket.worker_type),
        )

    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 3
    assert (
        tmp_db.execute(
            "SELECT count(*) FROM sprint_items "
            "WHERE sprint_id = 'sp_current' "
            "AND project_id = 'project_panels' AND kind = 'other'"
        ).fetchone()[0]
        == 1
    )


def test_production_planning_schedules_suppress_prelaid_tickets_without_backfill(
    tmp_db: Connection,
) -> None:
    _insert_current_sprint(tmp_db)
    schedule_ids = _production_planning_schedules(tmp_db)
    morning = _now("2026-07-28T05:05:00")
    prelaid_ids: dict[str, str] = {}
    for worker_type in schedule_ids:
        ticket = tickets_actions.create_ticket(
            tmp_db,
            title=f"Manual {worker_type}",
            actor="owner",
            now=int(morning.timestamp()) - 60,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type=worker_type,
            project_id="project_panels",
            planning_now=morning,
            boundary_hour=5,
        )
        prelaid_ids[worker_type] = ticket.id

    for missed_slot in (
        "2026-07-28T05:06:00",
        "2026-07-28T14:31:00",
        "2026-07-28T17:01:00",
    ):
        missed = _now(missed_slot)
        assert (
            actions.run_current_slot(
                tmp_db,
                planning_now=missed,
                now=int(missed.timestamp()),
                boundary_hour=5,
            )
            == []
        )
    assert all(
        data.list_occurrences(tmp_db, schedule_id) == []
        for schedule_id in schedule_ids.values()
    )

    morning_results = actions.run_current_slot(
        tmp_db,
        planning_now=morning,
        now=int(morning.timestamp()),
        boundary_hour=5,
    )
    midday = _now("2026-07-28T14:30:00")
    midday_results = actions.run_current_slot(
        tmp_db,
        planning_now=midday,
        now=int(midday.timestamp()),
        boundary_hour=5,
    )
    sprint_end = _now("2026-07-28T17:00:00")
    sprint_results = actions.run_current_slot(
        tmp_db,
        planning_now=sprint_end,
        now=int(sprint_end.timestamp()),
        boundary_hour=5,
    )
    repeated_midday = actions.run_current_slot(
        tmp_db,
        planning_now=midday.replace(second=30),
        now=int(midday.timestamp()) + 30,
        boundary_hour=5,
    )

    assert repeated_midday == midday_results
    results = morning_results + midday_results + sprint_results
    assert len(results) == 3
    assert {result.outcome for result in results} == {OccurrenceOutcome.suppressed}
    for result in results:
        schedule = data.read_schedule(tmp_db, result.schedule_id)
        assert result.ticket_id == prelaid_ids[schedule.template.worker_type]
        assert data.list_occurrences(tmp_db, result.schedule_id) == [result]
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 3


def test_production_sprint_schedule_only_qualifies_on_current_sprint_final_day(
    tmp_db: Connection,
) -> None:
    _insert_current_sprint(tmp_db)
    schedule_ids = _production_planning_schedules(tmp_db)
    before = _now("2026-07-27T17:00:00")

    before_results = actions.run_current_slot(
        tmp_db,
        planning_now=before,
        now=int(before.timestamp()),
        boundary_hour=5,
    )

    assert before_results == []
    assert data.list_occurrences(
        tmp_db, schedule_ids["planning-sprint"]
    ) == []

    final = _now("2026-07-28T17:00:00")
    final_results = actions.run_current_slot(
        tmp_db,
        planning_now=final,
        now=int(final.timestamp()),
        boundary_hour=5,
    )

    assert {
        tickets_data.read_ticket(tmp_db, str(result.ticket_id)).worker_type
        for result in final_results
    } == {"planning-sprint"}
    assert len(data.list_occurrences(tmp_db, schedule_ids["planning-sprint"])) == 1


def test_due_occurrence_creates_and_places_one_ordinary_ticket(
    tmp_db: Connection,
) -> None:
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
    assert ticket.ticket_status is TicketStatus.awaiting_approval
    kickoff_proposal = fields_codec.get_slot(ticket.fields, "kickoff").proposal
    assert kickoff_proposal is not None
    assert kickoff_proposal.body == "Gather evidence first."
    assert tmp_db.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = 'day_2026-07-28' AND ticket_id = ?",
        (ticket_id,),
    ).fetchone()
    assert len(data.list_occurrences(tmp_db, schedule_id)) == 1


def test_scheduled_ticket_can_explicitly_remain_in_backlog(tmp_db: Connection) -> None:
    tmp_db.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_current', 'Current', '2026-07-20', '2026-08-02', 0, 0)"
    )
    _schedule(
        tmp_db,
        template=_template(
            project_id="project_vylo",
            placement_mode=ScheduledTicketPlacementMode.backlog,
        ),
    )
    now = _now("2026-07-28T14:30:00")

    result = actions.run_current_slot(
        tmp_db,
        planning_now=now,
        now=int(now.timestamp()),
        boundary_hour=5,
    )

    ticket = tickets_data.read_ticket(tmp_db, str(result[0].ticket_id))
    assert ticket.sprint_item_id is None
    assert ticket.effective_sprint_id is None
    assert ticket.project_id == "project_vylo"
    assert (
        tmp_db.execute(
            "SELECT count(*) FROM sprint_items WHERE kind = 'other'"
        ).fetchone()[0]
        == 0
    )


def test_scheduled_ticket_can_target_an_explicit_sprint_item(
    tmp_db: Connection,
) -> None:
    tmp_db.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_current', 'Current', '2026-07-20', '2026-08-02', 0, 0)"
    )
    tmp_db.execute(
        "INSERT INTO sprint_items "
        "(id, title, priority, project_id, sprint_id, created_at, updated_at) "
        "VALUES ('si_target', 'Target', 'P0', 'project_vylo', 'sp_current', 0, 0)"
    )
    _schedule(
        tmp_db,
        template=_template(
            placement_mode=ScheduledTicketPlacementMode.sprint_item,
            sprint_item_id="si_target",
        ),
    )
    now = _now("2026-07-28T14:30:00")

    result = actions.run_current_slot(
        tmp_db,
        planning_now=now,
        now=int(now.timestamp()),
        boundary_hour=5,
    )

    ticket = tickets_data.read_ticket(tmp_db, str(result[0].ticket_id))
    assert ticket.sprint_item_id == "si_target"
    assert ticket.effective_sprint_id == "sp_current"
    assert ticket.project_id == "project_vylo"
    assert ticket.priority is Priority.P3


def test_schedule_placement_transitions_preserve_effective_project(
    tmp_db: Connection,
) -> None:
    tmp_db.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_current', 'Current', '2026-07-20', '2026-08-02', 0, 0)"
    )
    tmp_db.execute(
        "INSERT INTO sprint_items "
        "(id, title, project_id, sprint_id, created_at, updated_at) "
        "VALUES ('si_target', 'Target', 'project_vylo', 'sp_current', 0, 0)"
    )
    exact = _schedule(
        tmp_db,
        template=_template(
            placement_mode=ScheduledTicketPlacementMode.sprint_item,
            sprint_item_id="si_target",
        ),
    )

    backlog = actions.update_schedule(
        tmp_db,
        exact,
        {
            "placement_mode": ScheduledTicketPlacementMode.backlog,
            "sprint_item_id": None,
        },
        now=50,
    )
    assert backlog.template.project_id == "project_vylo"
    assert backlog.template.sprint_item_id is None

    exact_again = actions.update_schedule(
        tmp_db,
        exact,
        {
            "placement_mode": ScheduledTicketPlacementMode.sprint_item,
            "sprint_item_id": "si_target",
        },
        now=60,
    )
    assert exact_again.template.project_id is None
    assert exact_again.template.sprint_item_id == "si_target"

    current_sprint = actions.update_schedule(
        tmp_db,
        exact,
        {
            "placement_mode": ScheduledTicketPlacementMode.current_sprint,
            "sprint_item_id": None,
        },
        now=70,
    )
    assert current_sprint.template.project_id == "project_vylo"
    assert current_sprint.template.sprint_item_id is None


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
    schedule_id = _schedule(tmp_db, cadence=ScheduleCadence.current_sprint_final_day)
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
    assert result[0].ticket_id is not None
    ticket = tickets_data.read_ticket(tmp_db, result[0].ticket_id)
    assert ticket.effective_sprint_id == "sp_current"
    assert ticket.sprint_item_id is not None
    assert (
        tmp_db.execute(
            "SELECT kind FROM sprint_items WHERE id = ?", (ticket.sprint_item_id,)
        ).fetchone()["kind"]
        == "other"
    )
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
        backlog = client.post(
            "/api/schedules",
            json={
                "title": "Backlog schedule",
                "worker_type": "exploration",
                "local_time": "15:30",
                "sprint_item_id": None,
            },
        )
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
    assert backlog.status_code == 200, backlog.text
    assert created.json()["placement_mode"] == "current_sprint"
    assert backlog.json()["placement_mode"] == "backlog"
    assert unknown_type.status_code == 400
    assert unknown_type_update.status_code == 400
    assert updated.json()["enabled"] is False
    assert updated.json()["cadence"] == "current_sprint_final_day"
    assert shown.json()["occurrences"] == []
    assert {item["id"] for item in listed.json()["schedules"]} == {
        schedule_id,
        backlog.json()["id"],
    }


def test_runtime_loop_uses_injected_clock_and_restart_safe_receipt(
    tmp_path: Path,
) -> None:
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
            check.execute(
                "SELECT count(*) FROM scheduled_ticket_occurrences"
            ).fetchone()[0]
            == 1
        )
    finally:
        check.close()
