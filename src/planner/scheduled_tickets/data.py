"""SQLite persistence for scheduled Ticket configuration and occurrence receipts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import cast

from planner.core.contracts import Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import ID_PREFIXES, new_id
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketOccurrence,
    ScheduledTicketPlacementMode,
    ScheduledTicketSchedule,
    ScheduledTicketTemplate,
)

_SCHEDULE_COLUMNS = (
    "id, enabled, cadence, local_time, title, worker_type, kickoff_note, priority, "
    "deadline, project_id, placement_mode, sprint_item_id, employee_backend, "
    "employee_launch_model, blocked_by_ticket_ids, created_at, updated_at"
)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        conn.execute("SAVEPOINT scheduled_ticket_write")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO scheduled_ticket_write")
            conn.execute("RELEASE scheduled_ticket_write")
            raise
        else:
            conn.execute("RELEASE scheduled_ticket_write")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _blocked_by_from_json(raw: object) -> tuple[str, ...]:
    try:
        payload = json.loads(str(raw))
    except ValueError as exc:
        raise RuntimeError("scheduled Ticket blocker ids are corrupt") from exc
    if not isinstance(payload, list) or any(
        not isinstance(item, str) for item in payload
    ):
        raise RuntimeError("scheduled Ticket blocker ids are not a string list")
    return tuple(payload)


def _row_to_schedule(row: sqlite3.Row) -> ScheduledTicketSchedule:
    return ScheduledTicketSchedule(
        id=str(row["id"]),
        enabled=bool(row["enabled"]),
        cadence=ScheduleCadence(str(row["cadence"])),
        local_time=str(row["local_time"]),
        template=ScheduledTicketTemplate(
            title=str(row["title"]),
            worker_type=str(row["worker_type"]),
            kickoff_note=str(row["kickoff_note"]),
            priority=Priority(str(row["priority"])),
            deadline=None if row["deadline"] is None else str(row["deadline"]),
            project_id=None if row["project_id"] is None else str(row["project_id"]),
            placement_mode=ScheduledTicketPlacementMode(str(row["placement_mode"])),
            sprint_item_id=(
                None if row["sprint_item_id"] is None else str(row["sprint_item_id"])
            ),
            employee_backend=(
                None
                if row["employee_backend"] is None
                else str(row["employee_backend"])
            ),
            employee_launch_model=(
                None
                if row["employee_launch_model"] is None
                else str(row["employee_launch_model"])
            ),
            blocked_by_ticket_ids=_blocked_by_from_json(row["blocked_by_ticket_ids"]),
        ),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _row_to_occurrence(row: sqlite3.Row) -> ScheduledTicketOccurrence:
    return ScheduledTicketOccurrence(
        schedule_id=str(row["schedule_id"]),
        occurrence_key=str(row["occurrence_key"]),
        target_day_id=str(row["target_day_id"]),
        outcome=OccurrenceOutcome(str(row["outcome"])),
        ticket_id=None if row["ticket_id"] is None else str(row["ticket_id"]),
        error=None if row["error"] is None else str(row["error"]),
        created_at=int(row["created_at"]),
    )


def create_schedule(
    conn: sqlite3.Connection,
    *,
    enabled: bool,
    cadence: ScheduleCadence,
    local_time: str,
    template: ScheduledTicketTemplate,
    now: int,
) -> ScheduledTicketSchedule:
    schedule_id = new_id(ID_PREFIXES["schedule"])
    with transaction(conn):
        conn.execute(
            "INSERT INTO scheduled_ticket_schedules ("
            "id, enabled, cadence, local_time, title, worker_type, kickoff_note, priority, "
            "deadline, project_id, placement_mode, sprint_item_id, employee_backend, "
            "employee_launch_model, blocked_by_ticket_ids, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                schedule_id,
                int(enabled),
                cadence.value,
                local_time,
                template.title,
                template.worker_type,
                template.kickoff_note,
                template.priority.value,
                template.deadline,
                template.project_id,
                template.placement_mode.value,
                template.sprint_item_id,
                template.employee_backend,
                template.employee_launch_model,
                json.dumps(list(template.blocked_by_ticket_ids)),
                now,
                now,
            ),
        )
    return read_schedule(conn, schedule_id)


def list_schedules(conn: sqlite3.Connection) -> list[ScheduledTicketSchedule]:
    rows = conn.execute(
        f"SELECT {_SCHEDULE_COLUMNS} FROM scheduled_ticket_schedules "
        "ORDER BY local_time, created_at, id"
    ).fetchall()
    return [_row_to_schedule(row) for row in rows]


def list_enabled_for_time(
    conn: sqlite3.Connection, local_time: str
) -> list[ScheduledTicketSchedule]:
    rows = conn.execute(
        f"SELECT {_SCHEDULE_COLUMNS} FROM scheduled_ticket_schedules "
        "WHERE enabled = 1 AND local_time = ? ORDER BY id",
        (local_time,),
    ).fetchall()
    return [_row_to_schedule(row) for row in rows]


def read_schedule(
    conn: sqlite3.Connection, schedule_id: str
) -> ScheduledTicketSchedule:
    row = conn.execute(
        f"SELECT {_SCHEDULE_COLUMNS} FROM scheduled_ticket_schedules WHERE id = ?",
        (schedule_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.not_found,
            "scheduled Ticket schedule not found",
            {"id": schedule_id},
        )
    return _row_to_schedule(row)


_UPDATABLE_COLUMNS = frozenset(
    {
        "enabled",
        "cadence",
        "local_time",
        "title",
        "worker_type",
        "kickoff_note",
        "priority",
        "deadline",
        "project_id",
        "placement_mode",
        "sprint_item_id",
        "employee_backend",
        "employee_launch_model",
        "blocked_by_ticket_ids",
    }
)


def update_schedule(
    conn: sqlite3.Connection,
    schedule_id: str,
    values: Mapping[str, object],
    *,
    now: int,
) -> ScheduledTicketSchedule:
    if not values:
        return read_schedule(conn, schedule_id)
    if not set(values).issubset(_UPDATABLE_COLUMNS):
        raise ValueError("unknown scheduled Ticket schedule column")
    encoded = dict(values)
    if "enabled" in encoded:
        encoded["enabled"] = int(bool(encoded["enabled"]))
    cadence = encoded.get("cadence")
    if isinstance(cadence, ScheduleCadence):
        encoded["cadence"] = cadence.value
    priority = encoded.get("priority")
    if isinstance(priority, Priority):
        encoded["priority"] = priority.value
    placement_mode = encoded.get("placement_mode")
    if isinstance(placement_mode, ScheduledTicketPlacementMode):
        encoded["placement_mode"] = placement_mode.value
    if "blocked_by_ticket_ids" in encoded:
        blockers = cast(Iterable[object], encoded["blocked_by_ticket_ids"])
        encoded["blocked_by_ticket_ids"] = json.dumps(list(blockers))
    assignments = ", ".join(f"{column} = ?" for column in encoded)
    with transaction(conn):
        cursor = conn.execute(
            f"UPDATE scheduled_ticket_schedules SET {assignments}, updated_at = ? WHERE id = ?",
            (*encoded.values(), now, schedule_id),
        )
        if cursor.rowcount == 0:
            raise PlannerError(
                ErrorCode.not_found,
                "scheduled Ticket schedule not found",
                {"id": schedule_id},
            )
    return read_schedule(conn, schedule_id)


def read_occurrence(
    conn: sqlite3.Connection, schedule_id: str, occurrence_key: str
) -> ScheduledTicketOccurrence | None:
    row = conn.execute(
        "SELECT schedule_id, occurrence_key, target_day_id, outcome, ticket_id, error, "
        "created_at FROM scheduled_ticket_occurrences "
        "WHERE schedule_id = ? AND occurrence_key = ?",
        (schedule_id, occurrence_key),
    ).fetchone()
    return None if row is None else _row_to_occurrence(row)


def list_occurrences(
    conn: sqlite3.Connection, schedule_id: str
) -> list[ScheduledTicketOccurrence]:
    rows = conn.execute(
        "SELECT schedule_id, occurrence_key, target_day_id, outcome, ticket_id, error, "
        "created_at FROM scheduled_ticket_occurrences "
        "WHERE schedule_id = ? ORDER BY created_at DESC, occurrence_key DESC",
        (schedule_id,),
    ).fetchall()
    return [_row_to_occurrence(row) for row in rows]


def insert_occurrence(
    conn: sqlite3.Connection,
    *,
    schedule_id: str,
    occurrence_key: str,
    target_day_id: str,
    outcome: OccurrenceOutcome,
    ticket_id: str | None,
    error: str | None,
    now: int,
) -> ScheduledTicketOccurrence:
    conn.execute(
        "INSERT INTO scheduled_ticket_occurrences ("
        "schedule_id, occurrence_key, target_day_id, outcome, ticket_id, error, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            schedule_id,
            occurrence_key,
            target_day_id,
            outcome.value,
            ticket_id,
            error,
            now,
        ),
    )
    occurrence = read_occurrence(conn, schedule_id, occurrence_key)
    assert occurrence is not None
    return occurrence
