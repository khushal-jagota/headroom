"""Canonical chat state.

The gateway owns Hermes transport and persisted session transcripts. This module
owns the product-facing chat state the UI reads: durable visible messages plus at
most one active live turn per entity. It is generic over entity ids so ticket chat,
day chat, and the chief-of-staff chat all use the same state shape.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from planner.chat.contracts import ChatState, ChatStateMessage, ChatTurn
from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.core.ids import new_id


@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _row_to_message(row: sqlite3.Row) -> ChatStateMessage:
    return ChatStateMessage(
        id=int(row["id"]),
        role=str(row["role"]),
        text=str(row["text"]),
        created_at=int(row["created_at"]),
        turn_id=row["turn_id"],
    )


def _row_to_turn(row: sqlite3.Row) -> ChatTurn:
    return ChatTurn(
        id=str(row["id"]),
        entity_id=str(row["entity_id"]),
        origin=str(row["origin"]),
        mode=str(row["mode"]),
        status=str(row["status"]),
        phase=str(row["phase"]),
        activity_label=row["activity_label"],
        output_role=str(row["output_role"]),
        output_text=str(row["output_text"]),
        session_key=row["session_key"],
        error=row["error"],
        started_at=int(row["started_at"]),
        updated_at=int(row["updated_at"]),
        completed_at=row["completed_at"],
    )


def _append_message(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    turn_id: str | None,
    role: str,
    text: str,
    now: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO chat_messages (entity_id, turn_id, role, text, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (entity_id, turn_id, role, text, now),
    )
    inserted = cursor.lastrowid
    assert inserted is not None
    append_event(
        conn,
        entity_id,
        EventKind.chat_message_recorded,
        {"message_id": inserted, "turn_id": turn_id, "role": role},
        now,
    )
    return int(inserted)


def record_message(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    role: str,
    text: str,
    now: int,
    turn_id: str | None = None,
) -> int:
    """Append one visible chat message inside the caller's transaction."""
    return _append_message(conn, entity_id, turn_id=turn_id, role=role, text=text, now=now)


def read_state(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    session_key: str | None,
    legacy_messages: tuple[ChatStateMessage, ...] = (),
) -> ChatState:
    rows = conn.execute(
        "SELECT id, entity_id, turn_id, role, text, created_at "
        "FROM chat_messages WHERE entity_id = ? ORDER BY id",
        (entity_id,),
    ).fetchall()
    messages = tuple(_row_to_message(row) for row in rows)
    if not messages and legacy_messages:
        messages = legacy_messages
    active_turn = read_active_turn(conn, entity_id)
    return ChatState(messages=messages, active_turn=active_turn, session_key=session_key)


def read_active_turn(conn: sqlite3.Connection, entity_id: str) -> ChatTurn | None:
    turn_row = conn.execute(
        "SELECT * FROM chat_turns WHERE entity_id = ? AND status = 'running' "
        "ORDER BY started_at DESC, id DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    return _row_to_turn(turn_row) if turn_row is not None else None


def start_turn(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    origin: str,
    mode: str,
    visible_role: str,
    visible_text: str,
    output_role: str,
    phase: str,
    activity_label: str | None,
    now: int,
) -> ChatTurn:
    turn_id = new_id("run")
    with _txn(conn):
        try:
            conn.execute(
                "INSERT INTO chat_turns ("
                "id, entity_id, origin, mode, status, phase, activity_label, output_role, "
                "output_text, session_key, error, started_at, updated_at, completed_at"
                ") VALUES (?, ?, ?, ?, 'running', ?, ?, ?, '', NULL, NULL, ?, ?, NULL)",
                (
                    turn_id,
                    entity_id,
                    origin,
                    mode,
                    phase,
                    activity_label,
                    output_role,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PlannerError(
                ErrorCode.already_running,
                "chat turn is already running",
                {"entity_id": entity_id},
            ) from exc
        if visible_text:
            _append_message(
                conn,
                entity_id,
                turn_id=turn_id,
                role=visible_role,
                text=visible_text,
                now=now,
            )
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_started,
            {"turn_id": turn_id, "origin": origin, "mode": mode, "phase": phase},
            now,
        )
        row = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
        assert row is not None
        return _row_to_turn(row)


def attach_session_key(
    conn: sqlite3.Connection, turn_id: str, *, entity_id: str, session_key: str, now: int
) -> None:
    with _txn(conn):
        conn.execute(
            "UPDATE chat_turns SET session_key = ?, updated_at = ? WHERE id = ?",
            (session_key, now, turn_id),
        )
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_updated,
            {"turn_id": turn_id, "session_key": session_key},
            now,
        )


def set_turn_activity(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    phase: str,
    activity_label: str | None,
    now: int,
) -> None:
    with _txn(conn):
        cursor = conn.execute(
            "UPDATE chat_turns SET phase = ?, activity_label = ?, updated_at = ? "
            "WHERE id = ? AND status = 'running'",
            (phase, activity_label, now, turn_id),
        )
        if cursor.rowcount == 0:
            return
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_updated,
            {"turn_id": turn_id, "phase": phase, "activity_label": activity_label},
            now,
        )


def append_turn_output(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    delta: str,
    phase: str = "responding",
    now: int,
) -> None:
    if not delta:
        return
    with _txn(conn):
        cursor = conn.execute(
            "UPDATE chat_turns SET output_text = output_text || ?, phase = ?, "
            "activity_label = NULL, updated_at = ? WHERE id = ? AND status = 'running'",
            (delta, phase, now, turn_id),
        )
        if cursor.rowcount == 0:
            return
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_updated,
            {"turn_id": turn_id, "phase": phase},
            now,
        )


def finish_turn(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    reply_text: str,
    output_role: str,
    status: str = "complete",
    now: int,
) -> ChatTurn | None:
    with _txn(conn):
        row = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
        if row is None:
            raise PlannerError(ErrorCode.not_found, "chat turn not found", {"turn_id": turn_id})
        if row["status"] != "running":
            return _row_to_turn(row)
        final_text = reply_text or str(row["output_text"])
        conn.execute(
            "UPDATE chat_turns SET status = ?, phase = 'settled', activity_label = NULL, "
            "output_role = ?, output_text = ?, updated_at = ?, completed_at = ? WHERE id = ?",
            (status, output_role, final_text, now, now, turn_id),
        )
        if final_text:
            _append_message(
                conn,
                entity_id,
                turn_id=turn_id,
                role=output_role,
                text=final_text,
                now=now,
            )
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_finished,
            {"turn_id": turn_id, "status": status},
            now,
        )
        updated = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
        return _row_to_turn(updated) if updated is not None else None


def fail_turn(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    error: str,
    now: int,
) -> ChatTurn | None:
    with _txn(conn):
        row = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
        if row is None:
            raise PlannerError(ErrorCode.not_found, "chat turn not found", {"turn_id": turn_id})
        if row["status"] != "running":
            return _row_to_turn(row)
        conn.execute(
            "UPDATE chat_turns SET status = 'errored', phase = 'settled', "
            "activity_label = NULL, error = ?, updated_at = ?, completed_at = ? WHERE id = ?",
            (error, now, now, turn_id),
        )
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_finished,
            {"turn_id": turn_id, "status": "errored", "error": error},
            now,
        )
        updated = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
        return _row_to_turn(updated) if updated is not None else None
