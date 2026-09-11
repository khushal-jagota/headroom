"""Canonical feedback-note state transitions."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from planner.core.errors import ErrorCode, PlannerError
from planner.feedback import data
from planner.feedback.contracts import FeedbackNote
from planner.feedback.logic import (
    require_feedback_ids,
    require_feedback_text,
    require_page_context,
)


def create_feedback(
    conn: sqlite3.Connection,
    *,
    text: str,
    page_address: str | None,
    page_label: str | None,
    now: int,
) -> FeedbackNote:
    page_address, page_label = require_page_context(page_address, page_label)
    with data.transaction(conn):
        return data.create_note(
            conn,
            text=require_feedback_text(text),
            page_address=page_address,
            page_label=page_label,
            now=now,
        )


def dismiss_feedback(
    conn: sqlite3.Connection, feedback_id: str, *, now: int
) -> FeedbackNote:
    with data.transaction(conn):
        return data.set_handled(conn, (feedback_id,), ticket_id=None, now=now)[0]


def reopen_feedback(
    conn: sqlite3.Connection, feedback_id: str, *, now: int
) -> FeedbackNote:
    with data.transaction(conn):
        return data.reopen_note(conn, feedback_id, now=now)


def use_feedback(
    conn: sqlite3.Connection,
    feedback_ids: list[str],
    *,
    ticket_id: str,
    now: int,
    admit: Callable[[], None] | None = None,
) -> list[FeedbackNote]:
    with data.transaction(conn):
        ids = require_feedback_ids(feedback_ids)
        if admit is not None:
            admit()
        ticket = conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if ticket is None:
            raise PlannerError(ErrorCode.not_found, "ticket not found", {"id": ticket_id})
        return data.set_handled(conn, ids, ticket_id=ticket_id, now=now)
