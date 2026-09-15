"""Public feedback inbox projections."""

from __future__ import annotations

import sqlite3

from planner.core.contracts import JsonDict
from planner.feedback import data
from planner.feedback.contracts import FeedbackNote, FeedbackState


def note_json(note: FeedbackNote) -> JsonDict:
    return {
        "id": note.id,
        "text": note.text,
        "page_address": note.page_address,
        "page_label": note.page_label,
        "state": note.state.value,
        "ticket_id": note.ticket_id,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
        "handled_at": note.handled_at,
    }


def feedback_count_json(conn: sqlite3.Connection) -> JsonDict:
    row = conn.execute(
        "SELECT COUNT(*) AS open_count FROM feedback_notes WHERE state = 'open'"
    ).fetchone()
    return {"open_count": int(row["open_count"])}


def feedback_inbox_json(conn: sqlite3.Connection) -> JsonDict:
    open_notes = data.list_notes(conn, FeedbackState.open)
    handled_notes = data.list_notes(conn, FeedbackState.handled)
    rows = conn.execute(
        "SELECT id, title, stage, ticket_status FROM tickets WHERE id IN "
        "(SELECT ticket_id FROM feedback_notes WHERE state = 'handled' AND ticket_id IS NOT NULL)"
    ).fetchall()
    tickets = {str(row["id"]): row for row in rows}
    notes_by_ticket: dict[str, list[FeedbackNote]] = {}
    dismissed: list[FeedbackNote] = []
    for note in handled_notes:
        if note.ticket_id is None or note.ticket_id not in tickets:
            dismissed.append(note)
        else:
            notes_by_ticket.setdefault(note.ticket_id, []).append(note)

    groups: list[JsonDict] = []
    ordered_ticket_ids = sorted(
        notes_by_ticket,
        key=lambda ticket_id: (
            -(notes_by_ticket[ticket_id][0].handled_at or 0),
            ticket_id,
        ),
    )
    for ticket_id in ordered_ticket_ids:
        ticket = tickets[ticket_id]
        groups.append(
            {
                "ticket": {
                    "id": ticket_id,
                    "title": str(ticket["title"]),
                    "stage": str(ticket["stage"]),
                    "ticket_status": str(ticket["ticket_status"]),
                },
                "notes": [note_json(note) for note in notes_by_ticket[ticket_id]],
            }
        )
    if dismissed:
        groups.append({"ticket": None, "notes": [note_json(note) for note in dismissed]})
    return {
        "open_count": len(open_notes),
        "open": [note_json(note) for note in open_notes],
        "handled_groups": groups,
    }
