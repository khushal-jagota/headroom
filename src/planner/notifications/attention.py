"""Durable source-side attention transitions.

Writers record transitions in their own transaction.  The projector only turns durable
rising edges into notification facts, so several commits can safely share one wake-up.
"""

from __future__ import annotations

import json
import sqlite3


def _record(
    conn: sqlite3.Connection,
    subject_kind: str,
    subject_id: str,
    notification_type: str,
    active: bool,
    occurred_at: int,
) -> None:
    row = conn.execute(
        "SELECT active, generation FROM notification_attention_state "
        "WHERE subject_kind=? AND subject_id=? AND notification_type=?",
        (subject_kind, subject_id, notification_type),
    ).fetchone()
    prior_active = bool(row["active"]) if row is not None else False
    generation = int(row["generation"]) if row is not None else 0
    if active == prior_active and row is not None:
        return
    if active:
        generation += 1
        conn.execute(
            "INSERT INTO notification_attention_edges"
            "(subject_kind, subject_id, notification_type, generation, occurred_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (subject_kind, subject_id, notification_type, generation, occurred_at),
        )
    conn.execute(
        "INSERT INTO notification_attention_state"
        "(subject_kind, subject_id, notification_type, active, generation) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(subject_kind, subject_id, notification_type) "
        "DO UPDATE SET active=excluded.active, generation=excluded.generation",
        (subject_kind, subject_id, notification_type, int(active), generation),
    )


def conversation_attention(
    conn: sqlite3.Connection,
    conversation_id: str | None = None,
) -> dict[str, tuple[bool, bool, int, int]]:
    where = "WHERE c.conversation_id = ? " if conversation_id is not None else ""
    parameters: tuple[str, ...] = (conversation_id,) if conversation_id is not None else ()
    rows = conn.execute(
        "SELECT c.conversation_id, c.latest_sequence, "
        "COALESCE(MAX(CASE WHEN e.kind = 'message_to_owner' THEN e.sequence END), 0) "
        "> c.owner_read_through_sequence AS unread_message, "
        "EXISTS (SELECT 1 FROM conversation_events asked WHERE "
        "asked.conversation_id = c.conversation_id AND asked.kind = 'permission_asked' "
        "AND NOT EXISTS (SELECT 1 FROM conversation_events answered WHERE "
        "answered.conversation_id = c.conversation_id AND answered.kind = 'permission_answered' "
        "AND json_extract(answered.payload, '$.ask_id') = "
        "json_extract(asked.payload, '$.ask_id') AND answered.sequence > asked.sequence)) "
        "OR EXISTS (SELECT 1 FROM conversation_events asked WHERE "
        "asked.conversation_id = c.conversation_id AND asked.kind = 'user_input_requested' "
        "AND NOT EXISTS (SELECT 1 FROM conversation_events answered WHERE "
        "answered.conversation_id = c.conversation_id "
        "AND answered.kind IN ('user_input_answered','user_input_failed') "
        "AND json_extract(answered.payload, '$.request_id') = "
        "json_extract(asked.payload, '$.request_id') AND answered.sequence > asked.sequence)) "
        "AS pending_ask, "
        "COALESCE((SELECT CASE WHEN recent.kind = 'turn_ended' "
        "AND json_extract(recent.payload, '$.ending') = 'failed' THEN 1 ELSE 0 END "
        "FROM conversation_events recent WHERE recent.conversation_id = c.conversation_id "
        "AND recent.kind IN ('prompt','turn_ended') "
        "AND recent.sequence > COALESCE((SELECT ack.through_sequence "
        "FROM conversation_error_acknowledgements ack "
        "WHERE ack.conversation_id = c.conversation_id), 0) "
        "ORDER BY recent.sequence DESC LIMIT 1), 0) AS errored, "
        "COALESCE(MAX(e.created_at), c.created_at) AS occurred_at "
        "FROM conversations c LEFT JOIN conversation_events e "
        "ON e.conversation_id = c.conversation_id "
        f"{where}GROUP BY c.conversation_id",
        parameters,
    ).fetchall()
    return {
        str(row["conversation_id"]): (
            bool(row["unread_message"]) or bool(row["pending_ask"]),
            bool(row["errored"]),
            int(row["latest_sequence"]),
            int(row["occurred_at"]),
        )
        for row in rows
    }


def capture_ticket_attention(conn: sqlite3.Connection, ticket_id: str, occurred_at: int) -> None:
    # Local import keeps the conversation storage and work-attention projection acyclic.
    from planner.work_attention import ticket_assignment_from_values

    row = conn.execute(
        "SELECT id, stage, worker_type, ticket_status, pending_proposal, ceiling_holder, "
        "conversation_id "
        "FROM tickets WHERE id=?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return
    conversation = conversation_attention(conn, row["conversation_id"]).get(
        str(row["conversation_id"]), (False, False, 0, 0)
    )
    holder = json.loads(str(row["ceiling_holder"]))
    owner_holds = holder.get("kind") == "owner"
    flags = {
        "awaiting_reply": conversation[0],
        "awaiting_approval": (
            str(row["ticket_status"]) == "awaiting_approval"
            and row["pending_proposal"] is not None
            and owner_holds
        ),
        "assigned": ticket_assignment_from_values(
            stage=str(row["stage"]),
            worker_type=str(row["worker_type"]),
            owner_holds_ceiling=owner_holds,
        ),
        "errored": str(row["ticket_status"]) == "errored" or conversation[1],
    }
    for notification_type, active in flags.items():
        _record(conn, "ticket", ticket_id, notification_type, active, occurred_at)


def capture_conversation_attention(
    conn: sqlite3.Connection, conversation_id: str, occurred_at: int
) -> None:
    attention = conversation_attention(conn, conversation_id).get(conversation_id)
    if attention is None:
        return
    for row in conn.execute("SELECT id FROM tickets WHERE conversation_id=?", (conversation_id,)):
        capture_ticket_attention(conn, str(row["id"]), occurred_at)
    for row in conn.execute(
        "SELECT CASE WHEN i.id IS NULL THEN 'agent' ELSE 'sprint_item' END subject_kind, "
        "COALESCE(i.id, a.agent_key) subject_id FROM agents a "
        "LEFT JOIN sprint_items i ON i.supervisor_agent_key=a.agent_key "
        "WHERE a.conversation_id=? AND (a.agent_key='chief_of_staff' OR i.id IS NOT NULL)",
        (conversation_id,),
    ):
        for notification_type, active in (
            ("awaiting_reply", attention[0]),
            ("errored", attention[1]),
        ):
            _record(
                conn,
                str(row["subject_kind"]),
                str(row["subject_id"]),
                notification_type,
                active,
                occurred_at,
            )


def reconcile_attention(
    conn: sqlite3.Connection,
    desired: dict[tuple[str, str, str], tuple[bool, int]],
) -> None:
    """Fallback for imports and maintenance writes outside canonical runtime doors."""
    prior = {
        (
            str(row["subject_kind"]),
            str(row["subject_id"]),
            str(row["notification_type"]),
        ): (
            bool(row["active"]),
            int(row["generation"]),
        )
        for row in conn.execute(
            "SELECT subject_kind, subject_id, notification_type, active, generation "
            "FROM notification_attention_state"
        )
    }
    for (subject_kind, subject_id, notification_type), (
        active,
        occurred_at,
    ) in desired.items():
        key = (subject_kind, subject_id, notification_type)
        prior_value = prior.get(key)
        prior_active, generation = prior_value or (False, 0)
        if prior_value is not None and active == prior_active:
            continue
        if active:
            generation += 1
            conn.execute(
                "INSERT INTO notification_attention_edges"
                "(subject_kind, subject_id, notification_type, generation, occurred_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (*key, generation, occurred_at),
            )
        conn.execute(
            "INSERT INTO notification_attention_state"
            "(subject_kind, subject_id, notification_type, active, generation) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(subject_kind, subject_id, notification_type) "
            "DO UPDATE SET active=excluded.active, generation=excluded.generation",
            (*key, int(active), generation),
        )
