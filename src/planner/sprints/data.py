"""Canonical sprint writers and sprint-item reads.

Sprint items store plain item fields only; their status is derived on read from
child tickets and blocking links.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import NamedTuple, cast

from planner.core import links as core_links
from planner.core.clock import Clock
from planner.core.contracts import EventKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.core.ids import ID_PREFIXES, new_id
from planner.sprints.contracts import (
    KICKOFF_FIELDS,
    MID_SPRINT_FIELDS,
    REVIEW_FIELDS,
    ItemStatus,
    Sprint,
    SprintItem,
)
from planner.sprints.logic import (
    DateRange,
    SprintItemChildStatus,
    derive_sprint_item_status,
    find_overlap,
)
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.contracts import WorkerTypeDefinition


class ItemRead(NamedTuple):
    item: SprintItem
    status: ItemStatus
    blocking_ticket_ids: list[str]
    blockers_cleared: bool


_ITEM_PLAIN_FIELDS: frozenset[str] = frozenset(
    {"title", "body", "priority", "deadline", "project_id"}
)
_SPRINT_TEXT_FIELDS: frozenset[str] = frozenset(
    KICKOFF_FIELDS + REVIEW_FIELDS + MID_SPRINT_FIELDS + ("name",)
)


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _row_to_sprint(row: sqlite3.Row) -> Sprint:
    return Sprint(
        id=row["id"],
        name=row["name"],
        date_start=row["date_start"],
        date_end=row["date_end"],
        limiting_factor=row["limiting_factor"],
        primary_bet=row["primary_bet"],
        supports=row["supports"],
        premortem=row["premortem"],
        mid_where_we_stand=row["mid_where_we_stand"],
        mid_whats_changed=row["mid_whats_changed"],
        mid_what_to_adjust=row["mid_what_to_adjust"],
        outcomes=row["outcomes"],
        solo_reflection=row["solo_reflection"],
        joint_discussion=row["joint_discussion"],
        updates_to_thinking=row["updates_to_thinking"],
        carry_forward=row["carry_forward"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_item(row: sqlite3.Row) -> SprintItem:
    return SprintItem(
        id=row["id"],
        title=row["title"],
        body=row["body"],
        priority=Priority(row["priority"]),
        deadline=row["deadline"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        sprint_id=row["sprint_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _load_sprint(conn: sqlite3.Connection, sprint_id: str) -> Sprint:
    row = conn.execute("SELECT * FROM sprints WHERE id = ?", (sprint_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "sprint not found", {"id": sprint_id})
    return _row_to_sprint(row)


def _load_item(conn: sqlite3.Connection, item_id: str) -> SprintItem:
    row = conn.execute(
        "SELECT sprint_items.*, projects.name AS project_name "
        "FROM sprint_items JOIN projects ON projects.id = sprint_items.project_id "
        "WHERE sprint_items.id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "sprint item not found", {"id": item_id})
    return _row_to_item(row)


# --- sprint writers -------------------------------------------------------------


def create_sprint(
    conn: sqlite3.Connection,
    *,
    name: str,
    date_start: str,
    date_end: str,
    limiting_factor: str = "",
    primary_bet: str = "",
    supports: str = "",
    premortem: str = "",
    clock: Clock,
) -> Sprint:
    if not name:
        raise PlannerError(ErrorCode.validation, "sprint name is required", {})
    if date_start > date_end:
        raise PlannerError(
            ErrorCode.validation,
            "date_start must not be after date_end",
            {"date_start": date_start, "date_end": date_end},
        )
    now = clock.now_unix()
    sprint_id = new_id(ID_PREFIXES["sprint"])
    with _tx(conn):
        existing = [
            DateRange(id=r["id"], date_start=r["date_start"], date_end=r["date_end"])
            for r in conn.execute("SELECT id, date_start, date_end FROM sprints")
        ]
        conflict = find_overlap(date_start, date_end, existing)
        if conflict is not None:
            raise PlannerError(
                ErrorCode.sprint_overlap,
                "sprint dates overlap",
                {"conflict_id": conflict, "date_start": date_start, "date_end": date_end},
            )
        conn.execute(
            "INSERT INTO sprints ("
            "id, name, date_start, date_end, limiting_factor, primary_bet, supports, "
            "premortem, outcomes, solo_reflection, joint_discussion, updates_to_thinking, "
            "carry_forward, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', '', '', '', ?, ?)",
            (
                sprint_id,
                name,
                date_start,
                date_end,
                limiting_factor,
                primary_bet,
                supports,
                premortem,
                now,
                now,
            ),
        )
        append_event(
            conn,
            sprint_id,
            EventKind.sprint_created,
            {"name": name, "date_start": date_start, "date_end": date_end},
            now,
        )
    return _load_sprint(conn, sprint_id)


def update_sprint_field(
    conn: sqlite3.Connection, sprint_id: str, field: str, value: str, *, clock: Clock
) -> Sprint:
    if field not in _SPRINT_TEXT_FIELDS:
        raise PlannerError(
            ErrorCode.validation,
            "field is not an editable sprint text field",
            {"field": field},
        )
    # Freeze retired (rev6): nothing in a sprint locks, so every text field
    # (kickoff / mid-sprint / review / name) is always editable. No admissibility gate.
    sprint = _load_sprint(conn, sprint_id)
    now = clock.now_unix()
    prev = getattr(sprint, field)
    with _tx(conn):
        conn.execute(
            f"UPDATE sprints SET {field} = ?, updated_at = ? WHERE id = ?",
            (value, now, sprint_id),
        )
        append_event(
            conn,
            sprint_id,
            EventKind.sprint_updated,
            {"field": field, "from": prev, "to": value},
            now,
        )
    return _load_sprint(conn, sprint_id)


def set_sprint_dates(
    conn: sqlite3.Connection,
    sprint_id: str,
    *,
    date_start: str | None,
    date_end: str | None,
    clock: Clock,
) -> Sprint:
    sprint = _load_sprint(conn, sprint_id)
    new_start = date_start if date_start is not None else sprint.date_start
    new_end = date_end if date_end is not None else sprint.date_end
    for label, value in (("date_start", new_start), ("date_end", new_end)):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise PlannerError(ErrorCode.validation, f"invalid {label}", {label: value}) from exc
    if new_start > new_end:
        raise PlannerError(
            ErrorCode.validation,
            "date_start must not be after date_end",
            {"date_start": new_start, "date_end": new_end},
        )
    others = [
        DateRange(id=str(r["id"]), date_start=str(r["date_start"]), date_end=str(r["date_end"]))
        for r in conn.execute(
            "SELECT id, date_start, date_end FROM sprints WHERE id != ?", (sprint_id,)
        ).fetchall()
    ]
    conflict = find_overlap(new_start, new_end, others)
    if conflict is not None:
        raise PlannerError(
            ErrorCode.sprint_overlap,
            "sprint dates overlap",
            {"conflict_id": conflict, "date_start": new_start, "date_end": new_end},
        )
    now = clock.now_unix()
    with _tx(conn):
        conn.execute(
            "UPDATE sprints SET date_start = ?, date_end = ?, updated_at = ? WHERE id = ?",
            (new_start, new_end, now, sprint_id),
        )
        if new_start != sprint.date_start:
            append_event(
                conn,
                sprint_id,
                EventKind.sprint_updated,
                {"field": "date_start", "from": sprint.date_start, "to": new_start},
                now,
            )
        if new_end != sprint.date_end:
            append_event(
                conn,
                sprint_id,
                EventKind.sprint_updated,
                {"field": "date_end", "from": sprint.date_end, "to": new_end},
                now,
            )
    return _load_sprint(conn, sprint_id)


# --- sprint-item writers --------------------------------------------------------


def create_item(
    conn: sqlite3.Connection,
    *,
    title: str,
    project_id: str,
    body: str = "",
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    clock: Clock,
) -> SprintItem:
    if not title:
        raise PlannerError(ErrorCode.validation, "item title is required", {})
    item_id = new_id(ID_PREFIXES["sprint_item"])
    now = clock.now_unix()
    with _tx(conn):
        conn.execute(
            "INSERT INTO sprint_items ("
            "id, title, body, priority, deadline, project_id, sprint_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                title,
                body,
                priority.value,
                deadline,
                project_id,
                sprint_id,
                now,
                now,
            ),
        )
        append_event(
            conn,
            item_id,
            EventKind.sprint_item_created,
            {"title": title, "project_id": project_id, "sprint_id": sprint_id},
            now,
        )
    return _load_item(conn, item_id)


def create_idea(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str,
    project_id: str | None,
    now: int,
) -> sqlite3.Row:
    if not title:
        raise PlannerError(ErrorCode.validation, "idea title is required", {})
    idea_id = new_id(ID_PREFIXES["idea"])
    with _tx(conn):
        conn.execute(
            "INSERT INTO ideas (id, title, body, project_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (idea_id, title, body, project_id, now, now),
        )
        append_event(conn, idea_id, EventKind.idea_created, {"title": title, "source": "api"}, now)
    row = cast(
        sqlite3.Row | None,
        conn.execute(
            "SELECT ideas.*, projects.name AS project_name "
            "FROM ideas LEFT JOIN projects ON projects.id = ideas.project_id "
            "WHERE ideas.id = ?",
            (idea_id,),
        ).fetchone(),
    )
    assert row is not None
    return row


def update_item_field(
    conn: sqlite3.Connection, item_id: str, field: str, value: str | None, *, clock: Clock
) -> SprintItem:
    if field not in _ITEM_PLAIN_FIELDS:
        raise PlannerError(
            ErrorCode.validation, "field is not an editable item field", {"field": field}
        )
    item = _load_item(conn, item_id)
    prev = getattr(item, field)
    stored: str | None = value
    if field == "priority":
        if value is None:
            raise PlannerError(ErrorCode.validation, "invalid priority", {"value": value})
        try:
            stored = Priority(value).value
        except ValueError as exc:
            raise PlannerError(ErrorCode.validation, "invalid priority", {"value": value}) from exc
    elif field == "project_id":
        if value is None:
            raise PlannerError(ErrorCode.validation, "invalid project_id", {"value": value})
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (value,)).fetchone() is None:
            raise PlannerError(ErrorCode.validation, "invalid project_id", {"project_id": value})
    now = clock.now_unix()
    with _tx(conn):
        conn.execute(
            f"UPDATE sprint_items SET {field} = ?, updated_at = ? WHERE id = ?",
            (stored, now, item_id),
        )
        append_event(
            conn,
            item_id,
            EventKind.item_updated,
            {"field": field, "from": prev, "to": stored},
            now,
        )
    return _load_item(conn, item_id)


def assign_item_sprint(
    conn: sqlite3.Connection, item_id: str, sprint_id: str | None, *, clock: Clock
) -> SprintItem:
    item = _load_item(conn, item_id)
    prev = item.sprint_id
    if sprint_id is not None:
        _load_sprint(conn, sprint_id)
    now = clock.now_unix()
    with _tx(conn):
        conn.execute(
            "UPDATE sprint_items SET sprint_id = ?, updated_at = ? WHERE id = ?",
            (sprint_id, now, item_id),
        )
        append_event(
            conn,
            item_id,
            EventKind.item_updated,
            {"field": "sprint_id", "from": prev, "to": sprint_id},
            now,
        )
    return _load_item(conn, item_id)


# --- reads ----------------------------------------------------------------------


def _child_stage_in_progress(worker_type_definition: WorkerTypeDefinition, stage: str) -> bool:
    """Per-type "in progress by stage": a non-terminal linear stage strictly past the
    type's first worker stage (its first real-work stage — needs_success for coding,
    needs_understanding for new_worker). Resolves the row's own definition so the pure
    ``derive_sprint_item_status`` consumes only a precomputed boolean.

    Keyed off first_worker_stage, NOT default_ceiling (which is now the leading
    needs_kickoff): the in-progress threshold is unchanged from before the ceiling
    decoupling.

    Terminality is checked FIRST so ``and`` short-circuits: ``dropped`` is outside the
    linear order and ``stage_index`` raises on it, so the index is never computed for a
    terminal (done/dropped) stage."""
    terminal = worker_type_definition.is_terminal(stage)
    first_worker_idx = worker_type_definition.stage_index(
        worker_type_definition.first_worker_stage()
    )
    return (not terminal) and worker_type_definition.stage_index(stage) > first_worker_idx


def read_item(conn: sqlite3.Connection, item_id: str) -> ItemRead:
    item = _load_item(conn, item_id)
    blocker_summary = core_links.blocker_summary(conn, item_id)
    blocking_ticket_ids = [row.ticket_id for row in blocker_summary.blocked_by]
    blockers_cleared = bool(blocking_ticket_ids) and not blocker_summary.blocked
    child_rows = conn.execute(
        """
        SELECT tickets.id, tickets.stage, tickets.ticket_status, tickets.worker_type
        FROM tickets
        WHERE tickets.sprint_item_id = ?
        ORDER BY tickets.id
        """,
        (item_id,),
    ).fetchall()
    registry = configured_worker_type_registry()
    children = [
        SprintItemChildStatus(
            stage=str(row["stage"]),
            ticket_status=str(row["ticket_status"]),
            blocked=core_links.blocker_summary(conn, str(row["id"])).blocked,
            stage_in_progress=_child_stage_in_progress(
                registry.require(str(row["worker_type"])), str(row["stage"])
            ),
        )
        for row in child_rows
    ]
    status = derive_sprint_item_status(
        directly_blocked=blocker_summary.blocked,
        children=children,
    )
    return ItemRead(
        item=item,
        status=status,
        blocking_ticket_ids=blocking_ticket_ids,
        blockers_cleared=blockers_cleared,
    )


def read_sprint(conn: sqlite3.Connection, sprint_id: str) -> Sprint:
    return _load_sprint(conn, sprint_id)
