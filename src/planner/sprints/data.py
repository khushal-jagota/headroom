"""Canonical sprint writers and sprint-item reads.

Outcomes store shared context independently of Sprint commitments.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import NamedTuple, cast

from planner.conversation.contracts import ConversationBackendKey
from planner.core import links as core_links
from planner.core.clock import Clock
from planner.core.contracts import Principal, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import ID_PREFIXES, new_id
from planner.sprints.contracts import (
    SPRINT_DOCUMENT_FIELDS,
    SPRINT_ITEM_SUPERVISOR_LAUNCH_DEFAULTS,
    Sprint,
    SprintItem,
    SprintItemDeletion,
    SprintItemKind,
    SprintItemSupervisorLaunchConfiguration,
)
from planner.sprints.logic import (
    DateRange,
    find_overlap,
)
from planner.tickets import worker_context as ticket_worker_context
from planner.tickets.logic import admission


class ItemRead(NamedTuple):
    item: SprintItem
    blocking_ticket_ids: list[str]
    blockers_cleared: bool


_ITEM_PLAIN_FIELDS: frozenset[str] = frozenset(
    {"title", "body", "priority", "deadline", "project_id"}
)
_SPRINT_TEXT_FIELDS: frozenset[str] = frozenset(SPRINT_DOCUMENT_FIELDS + ("primary_bet", "name"))
PERSONAL_PROJECT_ID = "project_personal"


def supervisor_agent_key(item_id: str) -> str:
    return f"sprint_item_supervisor_{item_id}"


def _create_supervisor(conn: sqlite3.Connection, item_id: str) -> str:
    agent_key = supervisor_agent_key(item_id)
    conn.execute("INSERT INTO agents(agent_key, conversation_id) VALUES (?, NULL)", (agent_key,))
    return agent_key


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        conn.execute("SAVEPOINT sprint_write")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO sprint_write")
            conn.execute("RELEASE sprint_write")
            raise
        else:
            conn.execute("RELEASE sprint_write")
        return
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
        primary_bet=row["primary_bet"],
        kickoff=row["kickoff"],
        checkpoint=row["checkpoint"],
        review=row["review"],
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
        supervisor_agent_key=str(row["supervisor_agent_key"]),
        supervisor_launch_configuration=SprintItemSupervisorLaunchConfiguration(
            employee_backend=ConversationBackendKey(str(row["supervisor_backend"])),
            employee_launch_model=str(row["supervisor_model"]),
            employee_launch_reasoning_effort=(
                str(row["supervisor_reasoning_effort"])
                if row["supervisor_reasoning_effort"] is not None
                else None
            ),
        ),
        kind=SprintItemKind(row["kind"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _load_sprint(conn: sqlite3.Connection, sprint_id: str) -> Sprint:
    row = conn.execute("SELECT * FROM sprints WHERE id = ?", (sprint_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "sprint not found", {"id": sprint_id})
    return _row_to_sprint(row)


def _set_child_ticket_placement_changed(conn: sqlite3.Connection, item_id: str) -> None:
    rows = conn.execute(
        "SELECT id FROM tickets WHERE sprint_item_id = ? ORDER BY id", (item_id,)
    ).fetchall()
    for row in rows:
        ticket_worker_context.set_ticket_placement_changed(conn, str(row["id"]))


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
    primary_bet: str = "",
    kickoff: str = "",
    checkpoint: str = "",
    review: str = "",
    clock: Clock,
    admit: Callable[[], None] | None = None,
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
        if admit is not None:
            admit()
        existing = [
            DateRange(id=r["id"], date_start=r["date_start"], date_end=r["date_end"])
            for r in conn.execute("SELECT id, date_start, date_end FROM sprints")
        ]
        conflict = find_overlap(date_start, date_end, existing)
        if conflict is not None:
            raise PlannerError(
                ErrorCode.sprint_overlap,
                "sprint dates overlap",
                {
                    "conflict_id": conflict,
                    "date_start": date_start,
                    "date_end": date_end,
                },
            )
        conn.execute(
            "INSERT INTO sprints ("
            "id, name, date_start, date_end, primary_bet, kickoff, checkpoint, review, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sprint_id,
                name,
                date_start,
                date_end,
                primary_bet,
                kickoff,
                checkpoint,
                review,
                now,
                now,
            ),
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
    _load_sprint(conn, sprint_id)
    now = clock.now_unix()
    with _tx(conn):
        conn.execute(
            f"UPDATE sprints SET {field} = ?, updated_at = ? WHERE id = ?",
            (value, now, sprint_id),
        )
    return _load_sprint(conn, sprint_id)


def update_sprint(
    conn: sqlite3.Connection,
    sprint_id: str,
    *,
    text_edits: dict[str, str],
    set_dates: bool,
    date_start: str | None,
    date_end: str | None,
    clock: Clock,
    admit: Callable[[], None] | None = None,
) -> Sprint:
    """Validate and apply one compound Sprint patch under one write lock."""
    invalid_fields = sorted(set(text_edits) - _SPRINT_TEXT_FIELDS)
    if invalid_fields:
        raise PlannerError(
            ErrorCode.validation,
            "field is not an editable sprint text field",
            {"field": invalid_fields[0]},
        )
    now = clock.now_unix()
    with _tx(conn):
        if admit is not None:
            admit()
        sprint = _load_sprint(conn, sprint_id)
        new_start = date_start if date_start is not None else sprint.date_start
        new_end = date_end if date_end is not None else sprint.date_end
        if set_dates:
            for label, value in (("date_start", new_start), ("date_end", new_end)):
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise PlannerError(
                        ErrorCode.validation, f"invalid {label}", {label: value}
                    ) from exc
            if new_start > new_end:
                raise PlannerError(
                    ErrorCode.validation,
                    "date_start must not be after date_end",
                    {"date_start": new_start, "date_end": new_end},
                )
            others = [
                DateRange(
                    id=str(row["id"]),
                    date_start=str(row["date_start"]),
                    date_end=str(row["date_end"]),
                )
                for row in conn.execute(
                    "SELECT id, date_start, date_end FROM sprints WHERE id != ?",
                    (sprint_id,),
                )
            ]
            conflict = find_overlap(new_start, new_end, others)
            if conflict is not None:
                raise PlannerError(
                    ErrorCode.sprint_overlap,
                    "sprint dates overlap",
                    {
                        "conflict_id": conflict,
                        "date_start": new_start,
                        "date_end": new_end,
                    },
                )

        assignments = list(text_edits)
        values: list[str | int] = [text_edits[field] for field in assignments]
        if set_dates:
            assignments.extend(("date_start", "date_end"))
            values.extend((new_start, new_end))
        if assignments:
            set_clause = ", ".join(f"{field} = ?" for field in assignments)
            conn.execute(
                f"UPDATE sprints SET {set_clause}, updated_at = ? WHERE id = ?",
                (*values, now, sprint_id),
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
        DateRange(
            id=str(r["id"]),
            date_start=str(r["date_start"]),
            date_end=str(r["date_end"]),
        )
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
    clock: Clock,
) -> SprintItem:
    if not title:
        raise PlannerError(ErrorCode.validation, "item title is required", {})
    item_id = new_id(ID_PREFIXES["sprint_item"])
    now = clock.now_unix()
    with _tx(conn):
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )
        agent_key = _create_supervisor(conn, item_id)
        defaults = SPRINT_ITEM_SUPERVISOR_LAUNCH_DEFAULTS
        conn.execute(
            "INSERT INTO sprint_items ("
            "id, title, body, priority, deadline, project_id, "
            "supervisor_agent_key, supervisor_backend, supervisor_model, "
            "supervisor_reasoning_effort, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                title,
                body,
                priority.value,
                deadline,
                project_id,
                agent_key,
                defaults.employee_backend.value,
                defaults.employee_launch_model,
                defaults.employee_launch_reasoning_effort,
                now,
                now,
            ),
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


def update_supervisor_launch_configuration(
    conn: sqlite3.Connection,
    item_id: str,
    configuration: SprintItemSupervisorLaunchConfiguration,
    *,
    principal: Principal,
    clock: Clock,
) -> SprintItem:
    """Save the complete configuration that an accepted supervisor message used."""
    admission.require_direct_principal(principal, "update_supervisor_launch_configuration")
    _load_item(conn, item_id)
    with _tx(conn):
        conn.execute(
            "UPDATE sprint_items SET supervisor_backend=?, supervisor_model=?, "
            "supervisor_reasoning_effort=?, updated_at=? WHERE id=?",
            (
                configuration.employee_backend.value,
                configuration.employee_launch_model,
                configuration.employee_launch_reasoning_effort,
                clock.now_unix(),
                item_id,
            ),
        )
    return _load_item(conn, item_id)


def update_item_field(
    conn: sqlite3.Connection,
    item_id: str,
    field: str,
    value: str | None,
    *,
    clock: Clock,
) -> SprintItem:
    if field not in _ITEM_PLAIN_FIELDS:
        raise PlannerError(
            ErrorCode.validation,
            "field is not an editable item field",
            {"field": field},
        )
    _load_item(conn, item_id)
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
        if field == "project_id":
            conn.execute(
                "UPDATE tickets SET project_id = ?, updated_at = ? WHERE sprint_item_id = ?",
                (stored, now, item_id),
            )
            conn.execute(
                "UPDATE scheduled_ticket_schedules SET project_id = ?, updated_at = ? "
                "WHERE sprint_item_id = ?",
                (stored, now, item_id),
            )
            _set_child_ticket_placement_changed(conn, item_id)
    return _load_item(conn, item_id)


def update_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    edits: dict[str, str | None],
    clock: Clock,
    admit: Callable[[], None] | None = None,
) -> SprintItem:
    """Validate references and apply one compound Sprint Item patch atomically."""
    invalid_fields = sorted(set(edits) - _ITEM_PLAIN_FIELDS)
    if invalid_fields:
        raise PlannerError(
            ErrorCode.validation,
            "field is not an editable item field",
            {"field": invalid_fields[0]},
        )
    stored_edits = dict(edits)
    if "priority" in stored_edits:
        raw_priority = stored_edits["priority"]
        if raw_priority is None:
            raise PlannerError(ErrorCode.validation, "invalid priority", {"value": None})
        try:
            stored_edits["priority"] = Priority(raw_priority).value
        except ValueError as exc:
            raise PlannerError(
                ErrorCode.validation, "invalid priority", {"value": raw_priority}
            ) from exc
    now = clock.now_unix()
    with _tx(conn):
        if admit is not None:
            admit()
        _load_item(conn, item_id)
        project_id = stored_edits.get("project_id")
        if "project_id" in stored_edits and (
            project_id is None
            or conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None
        ):
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )

        assignments = list(stored_edits)
        values: list[str | int | None] = [stored_edits[field] for field in assignments]
        if assignments:
            set_clause = ", ".join(f"{field} = ?" for field in assignments)
            conn.execute(
                f"UPDATE sprint_items SET {set_clause}, updated_at = ? WHERE id = ?",
                (*values, now, item_id),
            )
            if "project_id" in stored_edits:
                item = _load_item(conn, item_id)
                conn.execute(
                    "UPDATE tickets SET project_id = ?, updated_at = ? WHERE sprint_item_id = ?",
                    (item.project_id, now, item_id),
                )
                conn.execute(
                    "UPDATE scheduled_ticket_schedules SET project_id = ?, "
                    "updated_at = ? "
                    "WHERE sprint_item_id = ?",
                    (item.project_id, now, item_id),
                )
                _set_child_ticket_placement_changed(conn, item_id)
    return _load_item(conn, item_id)


def _require_item_can_delete(conn: sqlite3.Connection, item_id: str) -> SprintItem:
    item = _load_item(conn, item_id)
    ticket_ids = [
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM tickets WHERE sprint_item_id=? ORDER BY id", (item_id,)
        ).fetchall()
    ]
    if ticket_ids:
        raise PlannerError(
            ErrorCode.validation,
            "sprint item has child tickets",
            {"sprint_item_id": item_id, "ticket_ids": ticket_ids},
        )
    return item


def _delete_item_rows(conn: sqlite3.Connection, item_id: str) -> SprintItemDeletion:
    """Delete one verified item inside the caller's transaction."""
    item = _require_item_can_delete(conn, item_id)
    held_ticket = conn.execute(
        "SELECT id FROM tickets "
        "WHERE json_extract(ceiling_holder, '$.kind') = 'sprint_item' "
        "AND json_extract(ceiling_holder, '$.id') = ? ORDER BY id LIMIT 1",
        (item_id,),
    ).fetchone()
    if held_ticket is not None:
        raise PlannerError(
            ErrorCode.validation,
            "Sprint Item cannot be deleted while it holds a Ticket ceiling",
            {"sprint_item_id": item_id, "held_ticket_id": str(held_ticket["id"])},
        )
    link_rows = conn.execute(
        "SELECT from_id, to_id, kind FROM links "
        "WHERE from_id = ? OR to_id = ? ORDER BY from_id, to_id, kind",
        (item_id, item_id),
    ).fetchall()
    linked_entity_ids = tuple(
        sorted(
            {
                str(row["to_id"] if row["from_id"] == item_id else row["from_id"])
                for row in link_rows
            }
        )
    )
    sprint_ids = tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT sprint_id FROM sprint_outcomes WHERE outcome_id=? ORDER BY sprint_id",
            (item_id,),
        )
    )

    for row in link_rows:
        conn.execute(
            "DELETE FROM links WHERE from_id = ? AND to_id = ? AND kind = ?",
            (str(row["from_id"]), str(row["to_id"]), str(row["kind"])),
        )

    conn.execute("DELETE FROM sprint_items WHERE id = ?", (item_id,))
    conn.execute("DELETE FROM agents WHERE agent_key = ?", (item.supervisor_agent_key,))
    return SprintItemDeletion(
        sprint_item_id=item_id,
        title=item.title,
        sprint_ids=sprint_ids,
        linked_entity_ids=linked_entity_ids,
    )


# --- reads ----------------------------------------------------------------------


def read_item(conn: sqlite3.Connection, item_id: str) -> ItemRead:
    item = _load_item(conn, item_id)
    blocker_summary = core_links.blocker_summary(conn, item_id)
    blocking_ticket_ids = [row.ticket_id for row in blocker_summary.blocked_by]
    blockers_cleared = bool(blocking_ticket_ids) and not blocker_summary.blocked
    return ItemRead(
        item=item,
        blocking_ticket_ids=blocking_ticket_ids,
        blockers_cleared=blockers_cleared,
    )


def read_sprint(conn: sqlite3.Connection, sprint_id: str) -> Sprint:
    return _load_sprint(conn, sprint_id)
