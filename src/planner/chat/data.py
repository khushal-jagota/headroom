"""Canonical chat state.

The gateway owns Hermes transport and persisted session transcripts. This module
owns the product-facing chat state the UI reads: durable visible messages plus at
most one active live turn per entity. It is generic over entity ids so ticket chat,
day chat, and the chief-of-staff chat all use the same state shape.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final, Literal

from planner.chat.contracts import (
    ChatActivityEntry,
    ChatActivityObservation,
    ChatPendingClarification,
    ChatState,
    ChatStateMessage,
    ChattableEntityKind,
    ChatTurn,
)
from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.core.ids import new_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import EmployeeSessionIdTransition

MAX_ACTIVE_TURN_ACTIVITY_ENTRIES: Final = 100


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


def _row_to_activity_entry(row: sqlite3.Row) -> ChatActivityEntry:
    return ChatActivityEntry(
        id=int(row["id"]),
        action_identity=row["action_identity"],
        category=str(row["category"]),
        label=str(row["label"]),
        lifecycle_state=str(row["lifecycle_state"]),
        started_at=int(row["started_at"]),
        updated_at=int(row["updated_at"]),
        completed_at=row["completed_at"],
    )


def _row_to_pending_clarification(row: sqlite3.Row) -> ChatPendingClarification | None:
    request_id = row["pending_clarification_request_id"]
    question = row["pending_clarification_question"]
    if request_id is None or question is None:
        return None
    choices_raw = row["pending_clarification_choices"]
    choices: tuple[str, ...] = ()
    if choices_raw:
        try:
            parsed = json.loads(str(choices_raw))
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            choices = tuple(str(item) for item in parsed if isinstance(item, str))
    return ChatPendingClarification(
        request_id=str(request_id),
        question=str(question),
        choices=choices,
    )


def _row_to_turn(
    row: sqlite3.Row, activity_entries: tuple[ChatActivityEntry, ...] = ()
) -> ChatTurn:
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
        can_pause=str(row["status"]) == "running" and row["session_key"] is not None,
        error=row["error"],
        started_at=int(row["started_at"]),
        updated_at=int(row["updated_at"]),
        completed_at=row["completed_at"],
        activity_entries=activity_entries,
        pending_clarification=_row_to_pending_clarification(row),
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
) -> ChatState:
    rows = conn.execute(
        "SELECT id, entity_id, turn_id, role, text, created_at "
        "FROM chat_messages WHERE entity_id = ? ORDER BY id",
        (entity_id,),
    ).fetchall()
    messages = tuple(_row_to_message(row) for row in rows)
    active_turn = read_active_turn(conn, entity_id)
    return ChatState(messages=messages, active_turn=active_turn)


def read_active_turn(conn: sqlite3.Connection, entity_id: str) -> ChatTurn | None:
    turn_row = conn.execute(
        "SELECT * FROM chat_turns WHERE entity_id = ? AND status = 'running' "
        "ORDER BY started_at DESC, id DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    if turn_row is None:
        return None
    activity_rows = conn.execute(
        "SELECT id, action_identity, category, label, lifecycle_state, "
        "started_at, updated_at, completed_at FROM chat_turn_activity_entries "
        "WHERE turn_id = ? ORDER BY id",
        (turn_row["id"],),
    ).fetchall()
    return _row_to_turn(
        turn_row, tuple(_row_to_activity_entry(row) for row in activity_rows)
    )


def read_running_turn_session_key(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
) -> str | None:
    row = conn.execute(
        "SELECT session_key FROM chat_turns "
        "WHERE id = ? AND entity_id = ? AND status = 'running'",
        (turn_id, entity_id),
    ).fetchone()
    return None if row is None else row["session_key"]


def record_turn_activity(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    observation: ChatActivityObservation,
    now: int,
) -> None:
    """Persist one normalized display-safe observation for a running turn."""
    with _txn(conn):
        running = conn.execute(
            "SELECT 1 FROM chat_turns WHERE id = ? AND status = 'running'", (turn_id,)
        ).fetchone()
        if running is None:
            return
        existing = None
        if observation.action_identity is not None:
            existing = conn.execute(
                "SELECT id FROM chat_turn_activity_entries "
                "WHERE turn_id = ? AND action_identity = ?",
                (turn_id, observation.action_identity),
            ).fetchone()
        else:
            latest = conn.execute(
                "SELECT id, category, label, lifecycle_state "
                "FROM chat_turn_activity_entries WHERE turn_id = ? ORDER BY id DESC LIMIT 1",
                (turn_id,),
            ).fetchone()
            if (
                latest is not None
                and latest["category"] == observation.category
                and latest["label"] == observation.label
                and latest["lifecycle_state"] == observation.lifecycle_state
            ):
                return
        completed_at = now if observation.lifecycle_state == "complete" else None
        if existing is None:
            conn.execute(
                "INSERT INTO chat_turn_activity_entries ("
                "turn_id, action_identity, category, label, lifecycle_state, "
                "started_at, updated_at, completed_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn_id,
                    observation.action_identity,
                    observation.category,
                    observation.label,
                    observation.lifecycle_state,
                    now,
                    now,
                    completed_at,
                ),
            )
        else:
            conn.execute(
                "UPDATE chat_turn_activity_entries SET category = ?, label = ?, "
                "lifecycle_state = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                (
                    observation.category,
                    observation.label,
                    observation.lifecycle_state,
                    now,
                    completed_at,
                    existing["id"],
                ),
            )
        conn.execute(
            "DELETE FROM chat_turn_activity_entries WHERE id IN ("
            "SELECT id FROM chat_turn_activity_entries WHERE turn_id = ? "
            "ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (turn_id, MAX_ACTIVE_TURN_ACTIVITY_ENTRIES),
        )
        conn.execute(
            "UPDATE chat_turns SET phase = 'doing', activity_label = ?, updated_at = ? "
            "WHERE id = ? AND status = 'running'",
            (observation.label, now, turn_id),
        )
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_updated,
            {"turn_id": turn_id, "phase": "doing", "activity_label": observation.label},
            now,
        )


def record_pending_clarification(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    clarification: ChatPendingClarification,
    now: int,
) -> bool:
    choices_json = json.dumps(list(clarification.choices)) if clarification.choices else None
    with _txn(conn):
        cursor = conn.execute(
            "UPDATE chat_turns SET pending_clarification_request_id = ?, "
            "pending_clarification_question = ?, pending_clarification_choices = ?, "
            "activity_label = NULL, updated_at = ? "
            "WHERE id = ? AND entity_id = ? AND status = 'running' "
            "AND origin = 'worker' AND mode = 'worker_step'",
            (
                clarification.request_id,
                clarification.question,
                choices_json,
                now,
                turn_id,
                entity_id,
            ),
        )
        if cursor.rowcount == 0:
            return False
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_updated,
            {
                "turn_id": turn_id,
                "pending_clarification_request_id": clarification.request_id,
            },
            now,
        )
        return True


def read_pending_clarification_answer_target(
    conn: sqlite3.Connection,
    entity_id: str,
    request_id: str,
) -> tuple[str, str, str, ChatPendingClarification]:
    row = conn.execute(
        "SELECT chat_turns.*, tickets.employee_session_id AS ticket_employee_session_id "
        "FROM chat_turns JOIN tickets ON tickets.id = chat_turns.entity_id "
        "WHERE chat_turns.entity_id = ? AND chat_turns.status = 'running' "
        "ORDER BY chat_turns.started_at DESC, chat_turns.id DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "no active worker chat turn")
    if str(row["origin"]) != "worker" or str(row["mode"]) != "worker_step":
        raise PlannerError(
            ErrorCode.validation,
            "active chat turn is not a worker step",
            {"entity_id": entity_id, "turn_id": str(row["id"])},
        )
    clarification = _row_to_pending_clarification(row)
    if clarification is None:
        raise PlannerError(
            ErrorCode.not_found,
            "active worker turn has no pending clarification",
            {"entity_id": entity_id, "turn_id": str(row["id"])},
        )
    if clarification.request_id != request_id:
        raise PlannerError(
            ErrorCode.already_running,
            "pending clarification request changed",
            {
                "entity_id": entity_id,
                "turn_id": str(row["id"]),
                "request_id": clarification.request_id,
            },
        )
    session_key = row["session_key"]
    employee_session_id = row["ticket_employee_session_id"]
    if not session_key or not employee_session_id or session_key != employee_session_id:
        raise PlannerError(
            ErrorCode.validation,
            "active worker turn session does not match the ticket employee session",
            {"entity_id": entity_id, "turn_id": str(row["id"])},
        )
    return str(row["id"]), str(session_key), str(employee_session_id), clarification


def mirror_accepted_clarification_answer(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    turn_id: str,
    request_id: str,
    question: str,
    answer: str,
    now: int,
) -> ChatTurn:
    with _txn(conn):
        row = conn.execute(
            "SELECT * FROM chat_turns WHERE id = ? AND entity_id = ?",
            (turn_id, entity_id),
        ).fetchone()
        if row is None:
            raise PlannerError(
                ErrorCode.not_found,
                "worker chat turn no longer exists",
                {"entity_id": entity_id, "turn_id": turn_id},
            )
        _append_message(
            conn,
            entity_id,
            turn_id=turn_id,
            role="assistant",
            text=question,
            now=now,
        )
        _append_message(
            conn,
            entity_id,
            turn_id=turn_id,
            role="human",
            text=answer,
            now=now,
        )
        conn.execute(
            "UPDATE chat_turns SET pending_clarification_request_id = NULL, "
            "pending_clarification_question = NULL, pending_clarification_choices = NULL, "
            "updated_at = ? WHERE id = ? AND entity_id = ? "
            "AND pending_clarification_request_id = ?",
            (now, turn_id, entity_id, request_id),
        )
        updated = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
        assert updated is not None
        append_event(
            conn,
            entity_id,
            EventKind.chat_turn_updated,
            {
                "turn_id": turn_id,
                "pending_clarification_request_id": updated[
                    "pending_clarification_request_id"
                ],
            },
            now,
        )
        return _row_to_turn(updated)


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
    with _txn(conn):
        return start_turn_in_transaction(
            conn,
            entity_id,
            origin=origin,
            mode=mode,
            visible_role=visible_role,
            visible_text=visible_text,
            output_role=output_role,
            phase=phase,
            activity_label=activity_label,
            now=now,
        )


def start_turn_in_transaction(
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
    """Insert one running turn inside the caller's existing write transaction."""
    turn_id = new_id("run")
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


def _settle_chat_turn_in_transaction(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    status: Literal["complete", "errored", "interrupted"],
    reply_text: str,
    output_role: Literal["assistant", "system"],
    error: str | None,
    now: int,
) -> ChatTurn:
    """Apply the one first-wins Chat settlement inside an existing transaction."""
    if status == "errored":
        if not error:
            raise PlannerError(ErrorCode.validation, "errored chat settlement requires error")
    elif error is not None:
        raise PlannerError(ErrorCode.validation, "successful chat settlement cannot have error")

    row = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
    if row is None or str(row["entity_id"]) != entity_id:
        raise PlannerError(ErrorCode.not_found, "chat turn not found", {"turn_id": turn_id})
    if row["status"] != "running":
        return _row_to_turn(row)

    conn.execute("DELETE FROM chat_turn_activity_entries WHERE turn_id = ?", (turn_id,))
    if status == "errored":
        final_text = str(row["output_text"])
        final_role = str(row["output_role"])
    else:
        final_text = reply_text or str(row["output_text"])
        final_role = output_role
    conn.execute(
        "UPDATE chat_turns SET status = ?, phase = 'settled', activity_label = NULL, "
        "pending_clarification_request_id = NULL, pending_clarification_question = NULL, "
        "pending_clarification_choices = NULL, output_role = ?, output_text = ?, error = ?, "
        "updated_at = ?, completed_at = ? WHERE id = ?",
        (status, final_role, final_text, error, now, now, turn_id),
    )
    if status != "errored" and final_text:
        _append_message(
            conn,
            entity_id,
            turn_id=turn_id,
            role=final_role,
            text=final_text,
            now=now,
        )
    event_payload: dict[str, object] = {"turn_id": turn_id, "status": status}
    if error is not None:
        event_payload["error"] = error
    append_event(conn, entity_id, EventKind.chat_turn_finished, event_payload, now)
    updated = conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
    assert updated is not None
    return _row_to_turn(updated)


def roll_running_turn_for_recovery(
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
    expected_session_key: str | None = None,
) -> ChatTurn | None:
    """Settle a stale visible turn and create one replacement recovery turn.

    This is a process-lifecycle writer: the old turn's partial output is preserved as
    an interrupted visible message, transient activity is cleared, and the fresh turn
    inherits the durable session key so the gateway can resume the same conversation.
    """
    turn_id = new_id("run")
    with _txn(conn):
        active = conn.execute(
            "SELECT * FROM chat_turns WHERE entity_id = ? AND status = 'running' "
            "ORDER BY started_at DESC, id DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if active is not None:
            if expected_session_key is not None:
                active_session_key = active["session_key"]
                if active_session_key is None:
                    error = (
                        "restart recovery stale turn has no session key "
                        "for existing session"
                    )
                elif active_session_key != expected_session_key:
                    error = (
                        "restart recovery stale turn session key does not match "
                        "existing session"
                    )
                else:
                    error = None
                if error is not None:
                    _settle_chat_turn_in_transaction(
                        conn,
                        str(active["id"]),
                        entity_id=entity_id,
                        status="errored",
                        reply_text="",
                        output_role="system"
                        if str(active["output_role"]) == "system"
                        else "assistant",
                        error=error,
                        now=now,
                    )
                    return None
            _settle_chat_turn_in_transaction(
                conn,
                str(active["id"]),
                entity_id=entity_id,
                status="interrupted",
                reply_text="",
                output_role="system"
                if str(active["output_role"]) == "system"
                else "assistant",
                error=None,
                now=now,
            )
            session_key = active["session_key"]
        else:
            session_key = None

        conn.execute(
            "INSERT INTO chat_turns ("
            "id, entity_id, origin, mode, status, phase, activity_label, output_role, "
            "output_text, session_key, error, started_at, updated_at, completed_at"
            ") VALUES (?, ?, ?, ?, 'running', ?, ?, ?, '', ?, NULL, ?, ?, NULL)",
            (
                turn_id,
                entity_id,
                origin,
                mode,
                phase,
                activity_label,
                output_role,
                session_key,
                now,
                now,
            ),
        )
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
            {"turn_id": turn_id, "can_pause": True},
            now,
        )


def bind_human_turn_session(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_kind: ChattableEntityKind,
    entity_id: str,
    expected_session_key: str | None,
    candidate_session_key: str,
    force_fresh_session: bool,
    now: int,
) -> str:
    """Causally bind the entity and its still-running human turn to one key."""
    table_by_kind: dict[ChattableEntityKind, str] = {
        "day": "days",
        "agent_chat_session": "agent_chat_sessions",
    }
    with _txn(conn):
        turn_row = conn.execute(
            "SELECT entity_id, origin, status, session_key FROM chat_turns WHERE id = ?",
            (turn_id,),
        ).fetchone()
        if (
            turn_row is None
            or str(turn_row["entity_id"]) != entity_id
            or str(turn_row["origin"]) != "human"
            or str(turn_row["status"]) != "running"
        ):
            raise PlannerError(
                ErrorCode.already_running,
                "human chat turn is no longer running",
                {"entity_id": entity_id, "turn_id": turn_id},
            )
        if entity_kind == "ticket":
            effective_session_key = tickets_data.write_employee_session_id_in_transaction(
                conn,
                entity_id,
                transition=EmployeeSessionIdTransition(
                    expected_employee_session_id=expected_session_key,
                    candidate_employee_session_id=candidate_session_key,
                ),
                force_fresh_employee_session=force_fresh_session,
                now=now,
            )
        else:
            table = table_by_kind[entity_kind]
            entity_row = conn.execute(
                f"SELECT chat_session_key FROM {table} WHERE id = ?", (entity_id,)
            ).fetchone()
            if entity_row is None:
                raise PlannerError(
                    ErrorCode.not_found,
                    "chattable entity not found",
                    {"entity_id": entity_id},
                )
            current_session_key: str | None = entity_row["chat_session_key"]
            if force_fresh_session:
                effective_session_key = candidate_session_key
            elif current_session_key == candidate_session_key:
                effective_session_key = candidate_session_key
            elif current_session_key == expected_session_key:
                effective_session_key = candidate_session_key
            elif current_session_key is not None:
                effective_session_key = current_session_key
            else:
                raise PlannerError(
                    ErrorCode.already_running,
                    "chat session changed during binding",
                    {"entity_id": entity_id, "turn_id": turn_id},
                )
            if current_session_key != effective_session_key:
                conn.execute(
                    f"UPDATE {table} SET chat_session_key = ?, updated_at = ? WHERE id = ?",
                    (effective_session_key, now, entity_id),
                )
                append_event(
                    conn,
                    entity_id,
                    EventKind.chat_session_created,
                    {"session_key": effective_session_key},
                    now,
                )
        if turn_row["session_key"] != effective_session_key:
            conn.execute(
                "UPDATE chat_turns SET session_key = ?, updated_at = ? WHERE id = ?",
                (effective_session_key, now, turn_id),
            )
            append_event(
                conn,
                entity_id,
                EventKind.chat_turn_updated,
                {"turn_id": turn_id, "can_pause": True},
                now,
            )
        return effective_session_key


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


def settle_chat_turn(
    conn: sqlite3.Connection,
    turn_id: str,
    *,
    entity_id: str,
    status: Literal["complete", "errored", "interrupted"],
    reply_text: str,
    output_role: Literal["assistant", "system"],
    error: str | None,
    now: int,
) -> ChatTurn:
    """Settle a visible Chat turn once; the first terminal transition wins."""
    with _txn(conn):
        return _settle_chat_turn_in_transaction(
            conn,
            turn_id,
            entity_id=entity_id,
            status=status,
            reply_text=reply_text,
            output_role=output_role,
            error=error,
            now=now,
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
        conn.execute("DELETE FROM chat_turn_activity_entries WHERE turn_id = ?", (turn_id,))
        conn.execute(
            "UPDATE chat_turns SET status = ?, phase = 'settled', activity_label = NULL, "
            "pending_clarification_request_id = NULL, pending_clarification_question = NULL, "
            "pending_clarification_choices = NULL, output_role = ?, output_text = ?, "
            "updated_at = ?, completed_at = ? WHERE id = ?",
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
        conn.execute("DELETE FROM chat_turn_activity_entries WHERE turn_id = ?", (turn_id,))
        conn.execute(
            "UPDATE chat_turns SET status = 'errored', phase = 'settled', "
            "activity_label = NULL, pending_clarification_request_id = NULL, "
            "pending_clarification_question = NULL, pending_clarification_choices = NULL, "
            "error = ?, updated_at = ?, completed_at = ? WHERE id = ?",
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
