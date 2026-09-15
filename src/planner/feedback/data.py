"""SQLite persistence for feedback notes."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import ID_PREFIXES, new_id
from planner.feedback.contracts import FeedbackNote, FeedbackState

_NOTE_COLUMNS = (
    "id, text, page_address, page_label, state, ticket_id, "
    "created_at, updated_at, handled_at"
)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        conn.execute("SAVEPOINT feedback_write")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO feedback_write")
            conn.execute("RELEASE feedback_write")
            raise
        else:
            conn.execute("RELEASE feedback_write")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _row_to_note(row: sqlite3.Row) -> FeedbackNote:
    return FeedbackNote(
        id=str(row["id"]),
        text=str(row["text"]),
        page_address=None if row["page_address"] is None else str(row["page_address"]),
        page_label=None if row["page_label"] is None else str(row["page_label"]),
        state=FeedbackState(str(row["state"])),
        ticket_id=None if row["ticket_id"] is None else str(row["ticket_id"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        handled_at=None if row["handled_at"] is None else int(row["handled_at"]),
    )


def create_note(
    conn: sqlite3.Connection,
    *,
    text: str,
    page_address: str | None,
    page_label: str | None,
    now: int,
) -> FeedbackNote:
    feedback_id = new_id(ID_PREFIXES["feedback"])
    conn.execute(
        "INSERT INTO feedback_notes "
        "(id, text, page_address, page_label, state, ticket_id, "
        "created_at, updated_at, handled_at) "
        "VALUES (?, ?, ?, ?, 'open', NULL, ?, ?, NULL)",
        (feedback_id, text, page_address, page_label, now, now),
    )
    return read_note(conn, feedback_id)


def read_note(conn: sqlite3.Connection, feedback_id: str) -> FeedbackNote:
    row = conn.execute(
        f"SELECT {_NOTE_COLUMNS} FROM feedback_notes WHERE id = ?", (feedback_id,)
    ).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.not_found, "feedback note not found", {"id": feedback_id}
        )
    return _row_to_note(row)


def list_notes(conn: sqlite3.Connection, state: FeedbackState) -> list[FeedbackNote]:
    rows = conn.execute(
        f"SELECT {_NOTE_COLUMNS} FROM feedback_notes WHERE state = ? "
        "ORDER BY created_at DESC, rowid DESC",
        (state.value,),
    ).fetchall()
    return [_row_to_note(row) for row in rows]


def set_handled(
    conn: sqlite3.Connection,
    feedback_ids: tuple[str, ...],
    *,
    ticket_id: str | None,
    now: int,
) -> list[FeedbackNote]:
    placeholders = ", ".join("?" for _ in feedback_ids)
    rows = conn.execute(
        f"SELECT id, state FROM feedback_notes WHERE id IN ({placeholders})",
        feedback_ids,
    ).fetchall()
    found = {str(row["id"]): str(row["state"]) for row in rows}
    missing = [feedback_id for feedback_id in feedback_ids if feedback_id not in found]
    if missing:
        raise PlannerError(ErrorCode.not_found, "feedback note not found", {"ids": missing})
    not_open = [feedback_id for feedback_id in feedback_ids if found[feedback_id] != "open"]
    if not_open:
        raise PlannerError(
            ErrorCode.validation, "feedback notes are already handled", {"ids": not_open}
        )
    conn.execute(
        f"UPDATE feedback_notes SET state = 'handled', ticket_id = ?, "
        f"handled_at = ?, updated_at = ? WHERE id IN ({placeholders})",
        (ticket_id, now, now, *feedback_ids),
    )
    return [read_note(conn, feedback_id) for feedback_id in feedback_ids]


def reopen_note(conn: sqlite3.Connection, feedback_id: str, *, now: int) -> FeedbackNote:
    current = read_note(conn, feedback_id)
    if current.state is FeedbackState.open:
        raise PlannerError(
            ErrorCode.validation, "feedback note is already open", {"id": feedback_id}
        )
    conn.execute(
        "UPDATE feedback_notes SET state = 'open', ticket_id = NULL, "
        "handled_at = NULL, updated_at = ? WHERE id = ?",
        (now, feedback_id),
    )
    return read_note(conn, feedback_id)
