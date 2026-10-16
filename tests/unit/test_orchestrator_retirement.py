"""The retirement preserves history and ordinary supply without live calendar state."""

from __future__ import annotations

from datetime import datetime
from sqlite3 import Connection

import pytest
from click.testing import CliRunner

from planner.cli.main import main
from planner.core.clock import TestClock as MutableClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.data import read_day
from planner.scheduled_tickets import actions, data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketPlacementMode,
    ScheduledTicketTemplate,
)
from planner.sprints import data as sprints_data


def _template() -> ScheduledTicketTemplate:
    return ScheduledTicketTemplate(
        title="Ordinary task",
        worker_type="personal",
        kickoff_note="Prepare one result",
        priority=Priority.P3,
        deadline=None,
        project_id="project_other",
        placement_mode=ScheduledTicketPlacementMode.backlog,
        sprint_item_id=None,
        employee_backend=None,
        employee_launch_model=None,
        blocked_by_ticket_ids=(),
        orchestrator_kind="chief",
    )


def test_daily_supply_uses_local_date_without_day_membership_and_replays_receipt(
    tmp_db: Connection,
) -> None:
    assert data.list_schedules(tmp_db) == []
    schedule = actions.create_schedule(
        tmp_db,
        enabled=True,
        cadence=ScheduleCadence.daily,
        local_time="03:30",
        template=_template(),
        now=100,
    )
    now = datetime.fromisoformat("2026-10-16T03:30:00+02:00")
    first = actions.run_current_slot(tmp_db, planning_now=now, now=200, boundary_hour=5)
    repeated = actions.run_current_slot(tmp_db, planning_now=now, now=201, boundary_hour=5)
    assert first == repeated
    assert first[0].outcome is OccurrenceOutcome.created
    assert first[0].target_day_id == "day_2026-10-16"
    assert len(data.list_occurrences(tmp_db, schedule.id)) == 1
    assert tmp_db.execute("SELECT count(*) FROM days").fetchone()[0] == 0
    assert tmp_db.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 0
    row = tmp_db.execute("SELECT orchestrator_kind, sprint_item_id FROM tickets").fetchone()
    assert tuple(row) == ("chief", None)


def test_retired_calendar_schedules_cannot_be_created(tmp_db: Connection) -> None:
    with pytest.raises(PlannerError, match="cadences are retired"):
        actions.create_schedule(
            tmp_db,
            enabled=True,
            cadence=ScheduleCadence.current_sprint_day_four,
            local_time="17:00",
            template=_template(),
            now=100,
        )


def test_missing_day_read_never_materializes_history(tmp_db: Connection) -> None:
    with pytest.raises(PlannerError) as missing:
        read_day(tmp_db, "day_2026-10-16", 100)
    assert missing.value.code is ErrorCode.not_found
    assert tmp_db.execute("SELECT count(*) FROM days").fetchone()[0] == 0
    tmp_db.execute(
        "INSERT INTO days (id,focus,created_at,updated_at) VALUES ('day_2026-10-15','Original',1,2)"
    )
    before = tmp_db.total_changes
    assert read_day(tmp_db, "day_2026-10-15", 999).focus == "Original"
    assert tmp_db.total_changes == before


def test_only_owner_can_settle_ordinary_orchestrator_and_chief_never_settles(
    tmp_db: Connection,
) -> None:
    clock = MutableClock(datetime.fromisoformat("2026-10-16T12:00:00+02:00"))
    item = sprints_data.create_item(
        tmp_db,
        title="Preserved identity",
        project_id="project_other",
        clock=clock,
    )
    assert not item.settled
    with pytest.raises(PlannerError, match="only the owner"):
        sprints_data.set_item_settled(
            tmp_db,
            item.id,
            settled=True,
            principal=CHIEF_PRINCIPAL,
            clock=clock,
        )
    settled = sprints_data.set_item_settled(
        tmp_db,
        item.id,
        settled=True,
        principal=OWNER_PRINCIPAL,
        clock=clock,
    )
    assert settled.settled
    assert settled.supervisor_agent_key == item.supervisor_agent_key
    with pytest.raises(PlannerError, match="Chief can never"):
        sprints_data.set_item_settled(
            tmp_db,
            "chief",
            settled=True,
            principal=OWNER_PRINCIPAL,
            clock=clock,
        )
    assert not sprints_data.set_item_settled(
        tmp_db,
        item.id,
        settled=False,
        principal=OWNER_PRINCIPAL,
        clock=clock,
    ).settled


def test_cli_keeps_historical_reads_and_ordinary_orchestrator_controls() -> None:
    runner = CliRunner()
    assert "create" not in runner.invoke(main, ["sprint", "--help"]).output
    assert "add-ticket" not in runner.invoke(main, ["day", "--help"]).output
    assert "show" in runner.invoke(main, ["day", "--help"]).output
    assert "settle" in runner.invoke(main, ["orchestrator", "--help"]).output
    assert "--chief" in runner.invoke(main, ["ticket", "assign", "--help"]).output


def test_managed_skill_retirement_preserves_custom_prose_and_detects_unknown_commands() -> None:
    from planner.core.migrations.retirement_skills import (
        corrected_sources,
        stale_calendar_instructions,
    )

    custom = (
        "---\nname: panels-sprint-item-supervisor\ndescription: Custom manager\n---\n\n"
        "Owner-specific judgment stays exactly as written.\n\n"
        "add-to-day and remove-from-day change Day membership.\n"
    )
    corrected = corrected_sources({"panels-sprint-item-supervisor": custom})
    text = corrected["panels-sprint-item-supervisor"]
    assert "Owner-specific judgment stays exactly as written." in text
    assert "Day membership operations are retired." in text
    assert stale_calendar_instructions(corrected) == []
    assert corrected_sources(corrected) == corrected
    assert stale_calendar_instructions(
        {"custom-skill": "Run panels day add-ticket for this Worker."}
    )
