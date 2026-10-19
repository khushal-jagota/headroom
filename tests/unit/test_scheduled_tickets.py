"""Generic exact-time scheduled Ticket creation, from cadence rule to handoff."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.backend_state import write_model_enablement
from planner.conversation.contracts import ConversationBackendKey
from planner.core.clock import TestClock as MutableClock
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.scheduled_tickets import actions, data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketPlacementMode,
    ScheduledTicketTemplate,
)
from planner.scheduled_tickets.logic import cadence_qualifies, validate_local_time
from planner.scheduled_tickets.runtime import ScheduledTicketLoop
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketStatus
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
    placement_mode: ScheduledTicketPlacementMode = ScheduledTicketPlacementMode.backlog,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
) -> ScheduledTicketTemplate:
    return ScheduledTicketTemplate(
        title=title,
        worker_type=worker_type,
        kickoff_note=kickoff_note,
        deadline=None,
        project_id=project_id,
        placement_mode=placement_mode,
        sprint_item_id=sprint_item_id,
        orchestrator_kind="sprint_item" if sprint_item_id is not None else "chief",
        employee_backend=None,
        employee_launch_model=None,
        blocked_by_ticket_ids=blocked_by,
        sprint_id=sprint_id,
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


def test_exact_time_and_cadence_rules_are_planning_neutral() -> None:
    assert validate_local_time("00:00") == "00:00"
    assert validate_local_time("23:59") == "23:59"
    with pytest.raises(PlannerError) as invalid:
        validate_local_time("7:30")
    assert invalid.value.code is ErrorCode.validation

    assert cadence_qualifies(ScheduleCadence.daily, planning_day="2026-07-28", sprint_ranges=())
    for retired in (
        ScheduleCadence.every_planning_day,
        ScheduleCadence.current_sprint_day_four,
        ScheduleCadence.current_sprint_final_day,
    ):
        assert not cadence_qualifies(retired, planning_day="2026-07-28", sprint_ranges=())


def test_due_occurrence_creates_one_ordinary_ticket_without_day_placement(
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
    kickoff_proposal = ticket.pending_proposal
    assert kickoff_proposal is not None
    assert kickoff_proposal.body == "Gather evidence first."
    assert tmp_db.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 0
    assert len(data.list_occurrences(tmp_db, schedule_id)) == 1


def test_pre_five_am_occurrence_uses_local_calendar_date(
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

    assert result[0].target_day_id == "day_2026-07-28"
    assert tmp_db.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 0


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
        planning_now=now,
        now=int(now.timestamp()),
        boundary_hour=5,
    )

    assert sorted(item.outcome.value for item in results) == ["created", "failed"]
    failure = data.list_occurrences(tmp_db, failed_schedule_id)[0]
    assert failure.outcome is OccurrenceOutcome.failed
    assert "blocking_ticket_id must be an existing ticket" in (failure.error or "")
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


@pytest.mark.parametrize("explicit", [False, True])
def test_schedule_rejects_disabled_model_without_partial_save(
    tmp_db: Connection,
    explicit: bool,
) -> None:
    profile = configured_worker_type_registry().require("coding").worker_profile
    backend = ConversationBackendKey(profile.default_backend)
    write_model_enablement(tmp_db, backend, profile.default_model, False)
    template = _template()
    if explicit:
        template = replace(
            template,
            employee_backend=str(backend),
            employee_launch_model=profile.default_model,
        )
    before = tuple(tmp_db.iterdump())
    with pytest.raises(PlannerError, match="disabled.*Choose an enabled model") as raised:
        _schedule(tmp_db, template=template)
    assert raised.value.code is ErrorCode.validation
    assert tuple(tmp_db.iterdump()) == before


@pytest.mark.parametrize("explicit", [False, True])
def test_schedule_model_disabled_after_save_fails_once_without_ticket(
    tmp_db: Connection,
    explicit: bool,
) -> None:
    profile = configured_worker_type_registry().require("coding").worker_profile
    backend = ConversationBackendKey(profile.default_backend)
    template = _template(title="Disabled occurrence")
    if explicit:
        template = replace(
            template,
            employee_backend=str(backend),
            employee_launch_model=profile.default_model,
        )
    failed_schedule_id = _schedule(tmp_db, template=template)
    saved_schedule = data.read_schedule(tmp_db, failed_schedule_id)
    write_model_enablement(tmp_db, backend, profile.default_model, False)
    tmp_db.execute(
        "INSERT OR REPLACE INTO model_variant_resolutions VALUES (?,?,?,?,?)",
        ("codex", "astra", "gpt-6-astra", '["medium", "high"]', int(time.time())),
    )
    enabled_schedule_id = _schedule(
        tmp_db,
        template=replace(
            _template(title="Enabled override occurrence", worker_type="exploration"),
            employee_backend=str(backend),
            employee_launch_model="gpt-6-astra",
        ),
    )
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
    assert failure.ticket_id is None
    assert "disabled" in (failure.error or "")
    assert "Choose an enabled model" in (failure.error or "")
    assert data.read_schedule(tmp_db, failed_schedule_id) == saved_schedule
    created = data.list_occurrences(tmp_db, enabled_schedule_id)[0]
    assert created.ticket_id is not None
    assert (
        tickets_data.read_ticket(tmp_db, created.ticket_id).employee_launch_model == "gpt-6-astra"
    )
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
    assert tmp_db.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 0
    assert (
        actions.run_current_slot(
            tmp_db,
            planning_now=now,
            now=int(now.timestamp()),
            boundary_hour=5,
        )
        == results
    )
    assert len(data.list_occurrences(tmp_db, failed_schedule_id)) == 1
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


def test_cold_runtime_preserves_stored_choice_and_restart_safe_receipt(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = tmp_path / "scheduled-loop.db"
    conn = connect(str(db_path))
    create_schema(conn)
    schedule_id = _schedule(
        conn,
        template=replace(
            _template(), employee_backend="codex", employee_launch_model="gpt-6-sol",
        ),
    )
    stored_schedule = data.read_schedule(conn, schedule_id)
    assert conn.execute("SELECT count(*) FROM model_catalogue_models").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM model_variant_resolutions").fetchone()[0] == 0
    conn.close()
    caplog.clear()
    clock = MutableClock(_now("2026-07-28T14:29:59"))

    # The ordinary synchronous loop needs no provider preparation for a first occurrence.
    first_loop = ScheduledTicketLoop(str(db_path), clock, boundary_hour=5)
    assert first_loop.poll_once() == []
    clock.set(_now("2026-07-28T14:30:00"))
    first = first_loop.poll_once()
    second_loop = ScheduledTicketLoop(str(db_path), clock, boundary_hour=5)
    second = second_loop.poll_once()

    assert first[0].outcome is OccurrenceOutcome.created
    assert second == first
    assert "resolution unavailable" in caplog.text
    # A restart after the next day's due minute does not replay a missed occurrence.
    clock.set(_now("2026-07-29T14:31:00"))
    assert second_loop.poll_once() == []
    check = connect(str(db_path))
    try:
        assert first[0].ticket_id is not None
        ticket = tickets_data.read_ticket(check, first[0].ticket_id)
        assert ticket.employee_backend == "codex"
        assert ticket.employee_launch_model == "gpt-6-sol"
        assert data.read_schedule(check, schedule_id) == stored_schedule
        assert data.list_occurrences(check, schedule_id) == first
        assert check.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
        assert check.execute("SELECT count(*) FROM scheduled_ticket_occurrences").fetchone()[0] == 1
        assert check.execute("SELECT count(*) FROM model_catalogue_models").fetchone()[0] == 0
        assert check.execute("SELECT count(*) FROM model_variant_resolutions").fetchone()[0] == 0
    finally:
        check.close()
