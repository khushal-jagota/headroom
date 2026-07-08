"""Canonical sprint writers: the single door for every sprint / sprint-item state
transition. Pure logic (imported from planner.sprints.logic) decides; this layer
enforces — mapping each verdict to a PlannerError or a SQL mutation plus its
event(s), all inside one BEGIN IMMEDIATE transaction. sqlite3, events and the clock
live here only. Column names equal the contract dataclass field names one-for-one."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from typing import NamedTuple, cast

from planner.core.clock import Clock
from planner.core.contracts import EventKind, Priority, Project
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.core.ids import ID_PREFIXES, new_id
from planner.sprints.contracts import (
    KICKOFF_FIELDS,
    MID_SPRINT_FIELDS,
    PROPOSAL_ONLY_STATUSES,
    REVIEW_FIELDS,
    ItemStatus,
    ItemStatusProposal,
    Sprint,
    SprintItem,
)
from planner.sprints.logic import (
    DateRange,
    TransitionVerdict,
    blockers_cleared,
    classify_agent_transition,
    classify_human_transition,
    find_overlap,
)


class ItemRead(NamedTuple):
    item: SprintItem
    blockers_cleared: bool


_ITEM_PLAIN_FIELDS: frozenset[str] = frozenset(
    {"title", "body", "priority", "deadline", "project"}
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


def _proposal_to_json(p: ItemStatusProposal) -> str:
    return json.dumps(
        {
            "to_status": p.to_status.value,
            "note": p.note,
            "proposed_by": p.proposed_by,
            "created_at": p.created_at,
        }
    )


def _proposal_from_json(raw: str | None) -> ItemStatusProposal | None:
    if raw is None:
        return None
    data = json.loads(raw)
    return ItemStatusProposal(
        to_status=ItemStatus(data["to_status"]),
        note=data["note"],
        proposed_by=data["proposed_by"],
        created_at=data["created_at"],
    )


def _row_to_item(row: sqlite3.Row) -> SprintItem:
    return SprintItem(
        id=row["id"],
        title=row["title"],
        body=row["body"],
        status=ItemStatus(row["status"]),
        priority=Priority(row["priority"]),
        deadline=row["deadline"],
        project=Project(row["project"]),
        sprint_id=row["sprint_id"],
        blocked_by=json.loads(row["blocked_by"]),
        status_proposal=_proposal_from_json(row["status_proposal"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _load_sprint(conn: sqlite3.Connection, sprint_id: str) -> Sprint:
    row = conn.execute("SELECT * FROM sprints WHERE id = ?", (sprint_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "sprint not found", {"id": sprint_id})
    return _row_to_sprint(row)


def _load_item(conn: sqlite3.Connection, item_id: str) -> SprintItem:
    row = conn.execute("SELECT * FROM sprint_items WHERE id = ?", (item_id,)).fetchone()
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
    project: Project,
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
            "id, title, body, status, priority, deadline, project, "
            "sprint_id, blocked_by, status_proposal, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', NULL, ?, ?)",
            (
                item_id,
                title,
                body,
                ItemStatus.todo.value,
                priority.value,
                deadline,
                project.value,
                sprint_id,
                now,
                now,
            ),
        )
        append_event(
            conn,
            item_id,
            EventKind.sprint_item_created,
            {"title": title, "project": project.value, "sprint_id": sprint_id},
            now,
        )
    return _load_item(conn, item_id)


def create_idea(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str,
    project: Project | None,
    now: int,
) -> sqlite3.Row:
    if not title:
        raise PlannerError(ErrorCode.validation, "idea title is required", {})
    idea_id = new_id(ID_PREFIXES["idea"])
    with _tx(conn):
        conn.execute(
            "INSERT INTO ideas (id, title, body, project, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (idea_id, title, body, project.value if project is not None else None, now, now),
        )
        append_event(conn, idea_id, EventKind.idea_created, {"title": title, "source": "api"}, now)
    row = cast(
        sqlite3.Row | None,
        conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone(),
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
    elif field == "project":
        if value is None:
            raise PlannerError(ErrorCode.validation, "invalid project", {"value": value})
        try:
            stored = Project(value).value
        except ValueError as exc:
            raise PlannerError(ErrorCode.validation, "invalid project", {"value": value}) from exc
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


def transition_item_status(
    conn: sqlite3.Connection,
    item_id: str,
    to_status: ItemStatus,
    *,
    clock: Clock,
    by_agent: bool = True,
    blocked_by: Sequence[str] | None = None,
) -> SprintItem:
    item = _load_item(conn, item_id)
    from_status = item.status
    intended: list[str] = list(blocked_by or []) if to_status is ItemStatus.blocked else []
    verdict = (
        classify_agent_transition(from_status, to_status, intended)
        if by_agent
        else classify_human_transition(from_status, to_status, intended)
    )
    if verdict is TransitionVerdict.forbidden:
        raise PlannerError(
            ErrorCode.item_transition_forbidden,
            "transition not permitted",
            {"from": from_status.value, "to": to_status.value},
        )
    if verdict is TransitionVerdict.missing_blockers:
        raise PlannerError(
            ErrorCode.validation,
            "blocked requires a non-empty blocked_by",
            {"to": "blocked"},
        )
    new_blocked_by = intended if to_status is ItemStatus.blocked else []
    cause = "agent" if by_agent else "human"
    now = clock.now_unix()
    with _tx(conn):
        conn.execute(
            "UPDATE sprint_items SET status = ?, blocked_by = ?, updated_at = ? WHERE id = ?",
            (to_status.value, json.dumps(new_blocked_by), now, item_id),
        )
        append_event(
            conn,
            item_id,
            EventKind.item_status_changed,
            {"from": from_status.value, "to": to_status.value, "cause": cause},
            now,
        )
    return _load_item(conn, item_id)


def propose_item_status(
    conn: sqlite3.Connection,
    item_id: str,
    to_status: ItemStatus,
    *,
    note: str | None,
    proposed_by: str,
    clock: Clock,
) -> SprintItem:
    if to_status not in PROPOSAL_ONLY_STATUSES:
        raise PlannerError(
            ErrorCode.validation,
            "only done/deferred_next_sprint are proposable",
            {"to": to_status.value},
        )
    item = _load_item(conn, item_id)
    if item.status in PROPOSAL_ONLY_STATUSES:
        raise PlannerError(
            ErrorCode.validation,
            "item status is terminal; no further proposals",
            {"status": item.status.value},
        )
    now = clock.now_unix()
    with _tx(conn):
        existing = item.status_proposal
        if existing is not None:
            append_event(
                conn,
                item_id,
                EventKind.proposal_superseded,
                {
                    "field": "status",
                    "replaced_body": {
                        "to_status": existing.to_status.value,
                        "note": existing.note,
                        "proposed_by": existing.proposed_by,
                        "created_at": existing.created_at,
                    },
                },
                now,
            )
        proposal = ItemStatusProposal(
            to_status=to_status, note=note, proposed_by=proposed_by, created_at=now
        )
        conn.execute(
            "UPDATE sprint_items SET status_proposal = ?, updated_at = ? WHERE id = ?",
            (_proposal_to_json(proposal), now, item_id),
        )
        append_event(
            conn,
            item_id,
            EventKind.proposal_filed,
            {
                "field": "status",
                "body": {"to_status": to_status.value, "note": note},
                "proposed_by": proposed_by,
            },
            now,
        )
    return _load_item(conn, item_id)


def accept_item_status(
    conn: sqlite3.Connection, item_id: str, *, clock: Clock, resolved_by: str = "human"
) -> SprintItem:
    item = _load_item(conn, item_id)
    if item.status_proposal is None:
        raise PlannerError(ErrorCode.validation, "no pending status proposal", {})
    to_status = item.status_proposal.to_status
    from_status = item.status
    now = clock.now_unix()
    with _tx(conn):
        conn.execute(
            "UPDATE sprint_items SET status = ?, status_proposal = NULL, "
            "blocked_by = '[]', updated_at = ? WHERE id = ?",
            (to_status.value, now, item_id),
        )
        append_event(
            conn,
            item_id,
            EventKind.proposal_accepted,
            {
                "field": "status",
                "body": {"to_status": to_status.value},
                "resolved_by": resolved_by,
                "edited": False,
            },
            now,
        )
        append_event(
            conn,
            item_id,
            EventKind.item_status_changed,
            {"from": from_status.value, "to": to_status.value, "cause": "accept"},
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


def read_item(conn: sqlite3.Connection, item_id: str) -> ItemRead:
    item = _load_item(conn, item_id)
    if item.blocked_by:
        placeholders = ", ".join("?" for _ in item.blocked_by)
        rows = conn.execute(
            f"SELECT id, state FROM tickets WHERE id IN ({placeholders})",
            tuple(item.blocked_by),
        ).fetchall()
        states = {row["id"]: row["state"] for row in rows}
        cleared = blockers_cleared(item.blocked_by, states)
    else:
        cleared = False
    return ItemRead(item=item, blockers_cleared=cleared)


def read_sprint(conn: sqlite3.Connection, sprint_id: str) -> Sprint:
    return _load_sprint(conn, sprint_id)
