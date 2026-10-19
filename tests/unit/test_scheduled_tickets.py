"""Generic exact-time scheduled Ticket creation, from cadence rule to handoff."""

from __future__ import annotations

from datetime import datetime
from sqlite3 import Connection

from tests.support.principals import OWNER_PRINCIPAL

from planner.scheduled_tickets import actions, data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketPlacementMode,
    ScheduledTicketTemplate,
)
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketStatus


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
