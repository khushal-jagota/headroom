"""The ``plan seed --demo`` dataset (§12): a deterministic-by-content sample —
one current sprint, three items, eight tickets spanning every state, one blocking
link, one parented pair, one planned day. Requires an empty database."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta

from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.core.ids import ID_PREFIXES, day_id, new_id

_TABLES = (
    "sprints", "sprint_items", "tickets", "days", "day_tickets",
    "ideas", "links", "events", "runs", "boundary_runs",
)


def seed_demo(conn: sqlite3.Connection) -> None:
    for table in _TABLES:
        if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0:
            raise PlannerError(
                ErrorCode.db_not_empty, "seed --demo requires an empty database", {"table": table}
            )
    now = int(time.time())
    today = datetime.now().astimezone().date()
    conn.execute("BEGIN IMMEDIATE")
    try:
        _build_demo(conn, now, today)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _build_demo(conn: sqlite3.Connection, now: int, today: date) -> None:
    sprint_id = _insert_sprint(conn, now, today)
    parent_id = _insert_item(
        conn, sprint_id, "Ship the demo feature end to end.", "active", "P1", "Vylo",
        "Parent item for the demo ticket pair.", now,
    )
    _insert_item(
        conn, sprint_id, "Research the search index options.", "todo", "P2", "Learning", "", now,
    )
    _insert_item(
        conn, sprint_id, "Retire the legacy export job.", "done", "P3", "Other", "", now,
    )

    plus = {n: (today + timedelta(days=n)).isoformat() for n in (1, 3, 7)}
    today_iso = today.isoformat()

    _insert_ticket(
        conn, now, title="Draft the onboarding email success criteria.", state="needs_success",
        priority="P2", deadline=plus[7], ceiling="needs_success", at_cap="propose",
        sprint_item_id=None, sprint_id=sprint_id, project="Vylo",
        fields=_fields("Draft the onboarding email success criteria.", ()),
    )
    _insert_ticket(
        conn, now, title="Choose the search indexing approach.", state="needs_approach",
        priority="P1", deadline=plus[3], ceiling="needs_plan", at_cap="propose",
        sprint_item_id=None, sprint_id=sprint_id, project="Learning",
        fields=_fields("Choose the search indexing approach.", ("success",)),
    )
    t3 = _insert_ticket(
        conn, now, title="Plan the demo feature rollout.", state="needs_plan",
        priority="P1", deadline=None, ceiling="in_progress", at_cap="propose",
        sprint_item_id=parent_id, sprint_id=None, project=None,
        fields=_fields("Plan the demo feature rollout.", ("success", "approach")),
    )
    _insert_link(conn, now, t3, parent_id, "belongs_to")
    t4 = _insert_ticket(
        conn, now, title="Implement the demo feature slice.", state="in_progress",
        priority="P0", deadline=plus[1], ceiling="needs_review", at_cap="propose",
        sprint_item_id=parent_id, sprint_id=None, project=None,
        fields=_fields("Implement the demo feature slice.", ("success", "approach", "plan")),
    )
    _insert_link(conn, now, t4, parent_id, "belongs_to")
    t5 = _insert_ticket(
        conn, now, title="Review the analytics dashboard numbers.", state="needs_review",
        priority="P2", deadline=today_iso, ceiling="done", at_cap="propose",
        sprint_item_id=None, sprint_id=sprint_id, project="Vylo",
        fields=_fields(
            "Review the analytics dashboard numbers.", ("success", "approach", "plan", "result"),
            result_notes="Check the conversion query joins before approving.",
        ),
    )
    _insert_ticket(
        conn, now, title="Write the release notes.", state="done",
        priority="P3", deadline=None, ceiling="done", at_cap="stop",
        sprint_item_id=None, sprint_id=sprint_id, project="Tribe",
        fields=_fields(
            "Write the release notes.", ("success", "approach", "plan", "result"),
        ),
    )
    _insert_ticket(
        conn, now, title="Prototype the voice input toggle.", state="dropped",
        priority="P3", deadline=None, ceiling="needs_approach", at_cap="stop",
        sprint_item_id=None, sprint_id=sprint_id, project="Vylo",
        fields=_fields("Prototype the voice input toggle.", ("success",)),
    )
    t8 = _insert_ticket(
        conn, now, title="Fix the flaky login test.", state="in_progress",
        priority="P0", deadline=today_iso, ceiling="in_progress", at_cap="stop",
        sprint_item_id=None, sprint_id=sprint_id, project="Vylo",
        fields=_fields("Fix the flaky login test.", ("success", "approach", "plan")),
    )
    _insert_link(conn, now, t8, t5, "blocks")

    _insert_day(conn, now, today, t4, t8, t5)


def _insert_sprint(conn: sqlite3.Connection, now: int, today: date) -> str:
    sprint_id = new_id(ID_PREFIXES["sprint"])
    date_start = (today - timedelta(days=3)).isoformat()
    date_end = (today + timedelta(days=10)).isoformat()
    conn.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, limiting_factor, primary_bet, "
        "supports, premortem, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sprint_id, "Demo Sprint", date_start, date_end,
            "Demo: one clear slice must ship.",
            "Demo: shipping the feature slice unblocks the review queue.",
            "- Demo support line.", "- Demo premortem line.", now, now,
        ),
    )
    append_event(
        conn, sprint_id, EventKind.sprint_created,
        {"name": "Demo Sprint", "date_start": date_start, "date_end": date_end, "source": "demo"},
        now,
    )
    return sprint_id


def _insert_item(
    conn: sqlite3.Connection,
    sprint_id: str,
    title: str,
    status: str,
    priority: str,
    project: str,
    body: str,
    now: int,
) -> str:
    item_id = new_id(ID_PREFIXES["sprint_item"])
    conn.execute(
        "INSERT INTO sprint_items (id, title, body, status, priority, deadline, project, "
        "current_state_note, sprint_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, title, body, status, priority, None, project, "", sprint_id, now, now),
    )
    append_event(
        conn, item_id, EventKind.sprint_item_created,
        {"title": title, "status": status, "sprint_id": sprint_id, "source": "demo"},
        now,
    )
    return item_id


def _fields(title: str, keys: tuple[str, ...], result_notes: str | None = None) -> str:
    slots: dict[str, dict[str, str | None]] = {
        name: {"value": None, "proposal": None, "notes": None}
        for name in ("success", "approach", "plan", "result")
    }
    for key in keys:
        slots[key]["value"] = f"Demo {key} for {title}"
    if result_notes is not None:
        slots["result"]["notes"] = result_notes
    return json.dumps(slots)


def _insert_ticket(
    conn: sqlite3.Connection,
    now: int,
    *,
    title: str,
    state: str,
    priority: str,
    deadline: str | None,
    ceiling: str,
    at_cap: str,
    sprint_item_id: str | None,
    sprint_id: str | None,
    project: str | None,
    fields: str,
) -> str:
    ticket_id = new_id(ID_PREFIXES["ticket"])
    conn.execute(
        "INSERT INTO tickets (id, title, state, priority, deadline, project, sprint_item_id, "
        "sprint_id, recap, ceiling, at_cap, auto_blocked, consecutive_failures, "
        "chat_session_key, alias, fields, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticket_id, title, state, priority, deadline, project, sprint_item_id, sprint_id,
            "", ceiling, at_cap, 0, 0, None, None, fields, now, now,
        ),
    )
    append_event(
        conn, ticket_id, EventKind.ticket_created,
        {"title": title, "state": state, "alias": None, "source": "demo"},
        now,
    )
    return ticket_id


def _insert_link(conn: sqlite3.Connection, now: int, from_id: str, to_id: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO links (from_id, to_id, kind) VALUES (?, ?, ?)", (from_id, to_id, kind)
    )
    append_event(
        conn, from_id, EventKind.link_added,
        {"from_id": from_id, "to_id": to_id, "kind": kind, "source": "demo"},
        now,
    )


def _insert_day(
    conn: sqlite3.Connection, now: int, today: date, t4: str, t8: str, t5: str
) -> None:
    did = day_id(today)
    plan_tree = {
        "root": {"focus": "Ship the demo feature slice.", "status": "accepted"},
        "children": [
            {
                "ticket_id": t4, "note": "Land the slice behind the flag.",
                "status": "accepted", "position": 0,
            },
            {
                "ticket_id": t8, "note": "Stabilise the login test before review.",
                "status": "proposed", "position": 1,
            },
        ],
    }
    conn.execute(
        "INSERT INTO days (id, brief, notes, plan, chat_session_key, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            did, "Demo day: ship the feature slice and clear the review queue.", "",
            json.dumps(plan_tree), None, now, now,
        ),
    )
    append_event(conn, did, EventKind.day_created, {"source": "demo"}, now)
    for ticket_id, position in ((t4, 0), (t8, 1), (t5, 2)):
        conn.execute(
            "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, ?)",
            (did, ticket_id, position),
        )
        append_event(
            conn, did, EventKind.day_ticket_added,
            {"ticket_id": ticket_id, "position": position, "cause": "demo", "source": "demo"},
            now,
        )
