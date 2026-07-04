"""The day data layer. Materializes a day on first read/write (no "missing day"
state), owns the ordered day-ticket list (contiguous positions from 0), plan JSON
storage, brief/notes writes, and applies the logic layer's plan-tree effects.
Every state transition here appends exactly one canonical event. No FastAPI/
pydantic; times come in as unix-second ints from the caller's clock."""

from __future__ import annotations

import json
import sqlite3

from planner.core.contracts import EventKind
from planner.core.events import append_event
from planner.days.contracts import Day, DayTicket, PlanTree
from planner.days.logic.effects import (
    AddTicketToDay,
    Effect,
    EmitEvent,
    ReplanChild,
    ReplanRequest,
    ReplanRoot,
)
from planner.days.logic.tree import tree_from_dict, tree_to_dict


def materialize_day(conn: sqlite3.Connection, day_id: str, now_unix: int) -> None:
    """§3.4: create the day row if absent (brief='', notes='', plan=NULL,
    chat_session_key=NULL, created_at=updated_at=now_unix) and append a
    day_created event. Idempotent: a present day → no write, no event."""
    if conn.execute("SELECT 1 FROM days WHERE id = ?", (day_id,)).fetchone() is not None:
        return
    conn.execute(
        "INSERT INTO days (id, brief, notes, plan, chat_session_key, created_at, updated_at) "
        "VALUES (?, '', '', NULL, NULL, ?, ?)",
        (day_id, now_unix, now_unix),
    )
    append_event(conn, day_id, EventKind.day_created, {}, now_unix)


def read_day(conn: sqlite3.Connection, day_id: str, now_unix: int) -> Day:
    """§3.4 'reading a nonexistent day materializes it empty'. Materializes, then
    SELECTs the row and builds a Day (plan parsed via tree_from_dict when set)."""
    materialize_day(conn, day_id, now_unix)
    row = conn.execute(
        "SELECT id, brief, notes, plan, chat_session_key, created_at, updated_at "
        "FROM days WHERE id = ?",
        (day_id,),
    ).fetchone()
    assert row is not None
    plan_json = row["plan"]
    return Day(
        id=row["id"],
        brief=row["brief"],
        notes=row["notes"],
        plan=tree_from_dict(json.loads(plan_json)) if plan_json is not None else None,
        chat_session_key=row["chat_session_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_day_tickets(conn: sqlite3.Connection, day_id: str) -> list[DayTicket]:
    """day_tickets for the day, ordered by position (contiguous from 0)."""
    rows = conn.execute(
        "SELECT day_id, ticket_id, position FROM day_tickets WHERE day_id = ? ORDER BY position",
        (day_id,),
    ).fetchall()
    return [
        DayTicket(day_id=row["day_id"], ticket_id=row["ticket_id"], position=row["position"])
        for row in rows
    ]


def load_plan(conn: sqlite3.Connection, day_id: str) -> PlanTree | None:
    """Parse days.plan JSON → PlanTree, or None (absent day or NULL plan)."""
    row = conn.execute("SELECT plan FROM days WHERE id = ?", (day_id,)).fetchone()
    if row is None or row["plan"] is None:
        return None
    return tree_from_dict(json.loads(row["plan"]))


def store_plan(
    conn: sqlite3.Connection, day_id: str, tree: PlanTree | None, now_unix: int
) -> None:
    """Materialize, then write days.plan = tree_to_dict(tree) JSON (or NULL) and
    bump updated_at. No event here (callers emit the specific plan_* event)."""
    materialize_day(conn, day_id, now_unix)
    plan_json = json.dumps(tree_to_dict(tree)) if tree is not None else None
    conn.execute(
        "UPDATE days SET plan = ?, updated_at = ? WHERE id = ?",
        (plan_json, now_unix, day_id),
    )


def store_judgment(
    conn: sqlite3.Connection, day_id: str, brief: str, tree: PlanTree, now_unix: int
) -> None:
    """Boundary success path: write days.brief + days.plan (serialized) +
    updated_at in one UPDATE. No day_updated event (that kind is reserved for
    manual edits); the boundary emits plan_proposed separately."""
    conn.execute(
        "UPDATE days SET brief = ?, plan = ?, updated_at = ? WHERE id = ?",
        (brief, json.dumps(tree_to_dict(tree)), now_unix, day_id),
    )


def add_day_ticket(
    conn: sqlite3.Connection, day_id: str, ticket_id: str, now_unix: int, cause: str = "manual"
) -> bool:
    """§3.4: append at end with the next contiguous position (= current count).
    Idempotent: if (day_id, ticket_id) already present → return False, no event.
    On add → INSERT, append day_ticket_added {ticket_id, position, cause}, bump
    updated_at, return True. Materializes the day first (write path)."""
    materialize_day(conn, day_id, now_unix)
    present = conn.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
        (day_id, ticket_id),
    ).fetchone()
    if present is not None:
        return False
    position = conn.execute(
        "SELECT COUNT(*) AS n FROM day_tickets WHERE day_id = ?", (day_id,)
    ).fetchone()["n"]
    conn.execute(
        "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, ?)",
        (day_id, ticket_id, position),
    )
    append_event(
        conn,
        day_id,
        EventKind.day_ticket_added,
        {"ticket_id": ticket_id, "position": position, "cause": cause},
        now_unix,
    )
    conn.execute("UPDATE days SET updated_at = ? WHERE id = ?", (now_unix, day_id))
    return True


def remove_day_ticket(
    conn: sqlite3.Connection, day_id: str, ticket_id: str, now_unix: int
) -> None:
    """§3.4: delete the association only (ticket state untouched). If nothing was
    deleted (rowcount 0) → no-op, no event, no re-pack. Else re-pack remaining
    positions to 0..n-1 in existing position order, append day_ticket_removed
    {ticket_id}, bump updated_at."""
    cursor = conn.execute(
        "DELETE FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
        (day_id, ticket_id),
    )
    if cursor.rowcount == 0:
        return
    survivors = conn.execute(
        "SELECT ticket_id FROM day_tickets WHERE day_id = ? ORDER BY position",
        (day_id,),
    ).fetchall()
    for new_position, row in enumerate(survivors):
        conn.execute(
            "UPDATE day_tickets SET position = ? WHERE day_id = ? AND ticket_id = ?",
            (new_position, day_id, row["ticket_id"]),
        )
    append_event(conn, day_id, EventKind.day_ticket_removed, {"ticket_id": ticket_id}, now_unix)
    conn.execute("UPDATE days SET updated_at = ? WHERE id = ?", (now_unix, day_id))


def set_brief(conn: sqlite3.Connection, day_id: str, brief: str, now_unix: int) -> None:
    """Manual brief edit: materialize, UPDATE brief + updated_at, append
    day_updated {field: "brief"}."""
    materialize_day(conn, day_id, now_unix)
    conn.execute(
        "UPDATE days SET brief = ?, updated_at = ? WHERE id = ?", (brief, now_unix, day_id)
    )
    append_event(conn, day_id, EventKind.day_updated, {"field": "brief"}, now_unix)


def set_notes(conn: sqlite3.Connection, day_id: str, notes: str, now_unix: int) -> None:
    """Manual notes edit: materialize, UPDATE notes + updated_at, append
    day_updated {field: "notes"}."""
    materialize_day(conn, day_id, now_unix)
    conn.execute(
        "UPDATE days SET notes = ?, updated_at = ? WHERE id = ?", (notes, now_unix, day_id)
    )
    append_event(conn, day_id, EventKind.day_updated, {"field": "notes"}, now_unix)


def apply_plan_effects(
    conn: sqlite3.Connection,
    day_id: str,
    new_tree: PlanTree | None,
    effects: list[Effect],
    now_unix: int,
) -> list[ReplanRequest]:
    """Persist the transform result and apply its effects. (1) store_plan writes
    days.plan (or NULL for reject-all). (2) For each effect in order:
    AddTicketToDay → add_day_ticket(cause="plan_accept") (idempotent);
    EmitEvent → append_event; ReplanRoot/ReplanChild → collected. (3) Return the
    collected replan requests for the stage-4 runtime to serialize + execute (R5).
    At stage 3 no adapter is called here."""
    store_plan(conn, day_id, new_tree, now_unix)
    replans: list[ReplanRequest] = []
    for effect in effects:
        if isinstance(effect, AddTicketToDay):
            add_day_ticket(conn, day_id, effect.ticket_id, now_unix, cause="plan_accept")
        elif isinstance(effect, EmitEvent):
            append_event(conn, day_id, effect.kind, effect.payload, now_unix)
        elif isinstance(effect, (ReplanRoot, ReplanChild)):
            replans.append(effect)
    return replans
