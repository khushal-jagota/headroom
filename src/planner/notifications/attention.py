"""Durable source-side attention transitions.

Writers record transitions in their own transaction.  Queueing only turns durable rising
edges into deliveries, so several commits can safely share one wake-up.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import NamedTuple

from planner.conversation.events import (
    ConversationEventPayload,
    ConversationTurnEnding,
    MessageToOwnerEventPayload,
    PermissionAnsweredEventPayload,
    PermissionAskedEventPayload,
    PromptEventPayload,
    TurnEndedEventPayload,
    UserInputAnsweredEventPayload,
    UserInputFailedEventPayload,
    UserInputRequestedEventPayload,
)
from planner.conversation.logic.pending_requests import (
    REQUEST_TERMINATING_EVENT_KIND,
    advance_pending_requests,
)
from planner.tickets import derivation
from planner.tickets.contracts import TicketStatus


class ConversationAttention(NamedTuple):
    """What one conversation is asking of its owner, one fact per thing it can ask.

    An unread message and a pending ask are separate because they are separate things.
    Held as one flag, the second one to arrive raised no edge and notified nobody, and
    an ask announced itself as a message.
    """

    unread_message: bool
    pending_ask: bool
    errored: bool
    latest_sequence: int
    occurred_at: int


NOTHING_WAITING = ConversationAttention(False, False, False, 0, 0)


@dataclass(frozen=True, slots=True)
class ConversationAttentionSnapshot:
    """The immutable conversation facts one write validates and advances in memory."""

    conversation_id: str
    latest_sequence: int
    owner_read_through_sequence: int
    error_acknowledged_through_sequence: int
    latest_message_sequence: int
    pending_permission_ask_ids: frozenset[str]
    pending_user_input_request_ids: frozenset[str]
    errored: bool
    occurred_at: int

    def attention(self) -> ConversationAttention:
        return ConversationAttention(
            unread_message=self.latest_message_sequence > self.owner_read_through_sequence,
            pending_ask=bool(
                self.pending_permission_ask_ids or self.pending_user_input_request_ids
            ),
            errored=self.errored,
            latest_sequence=self.latest_sequence,
            occurred_at=self.occurred_at,
        )


def conversation_attention_snapshot(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    transaction_open: bool = False,
) -> ConversationAttentionSnapshot | None:
    """Own a short read transaction by default, or borrow the caller's open transaction.

    The borrowed path leaves commit and rollback to the caller, including on read errors.
    """

    if transaction_open:
        if not conn.in_transaction:
            raise RuntimeError("attention snapshot requires an open source transaction")
    else:
        conn.execute("BEGIN")
    try:
        conversation = conn.execute(
            "SELECT c.latest_sequence, c.owner_read_through_sequence, c.created_at, "
            "COALESCE(a.through_sequence, 0) AS error_acknowledged_through_sequence "
            "FROM conversations c LEFT JOIN conversation_error_acknowledgements a "
            "ON a.conversation_id=c.conversation_id WHERE c.conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if conversation is None:
            return None
        event_rows = conn.execute(
            "SELECT sequence, kind, payload, created_at FROM conversation_events "
            "WHERE conversation_id=? ORDER BY sequence",
            (conversation_id,),
        ).fetchall()
    finally:
        if not transaction_open:
            conn.execute("COMMIT")

    pending_permission_ask_ids: frozenset[str] = frozenset()
    pending_user_input_request_ids: frozenset[str] = frozenset()
    latest_message_sequence = 0
    error_acknowledged_through_sequence = int(conversation["error_acknowledged_through_sequence"])
    errored = False
    occurred_at = int(conversation["created_at"])
    for row in event_rows:
        sequence = int(row["sequence"])
        kind = str(row["kind"])
        payload = json.loads(str(row["payload"]))
        occurred_at = max(occurred_at, int(row["created_at"]))
        if kind == "message_to_owner":
            latest_message_sequence = sequence
        pending_permission_ask_ids, pending_user_input_request_ids = advance_pending_requests(
            pending_permission_ask_ids,
            pending_user_input_request_ids,
            kind,
            ask_id=(
                str(payload["ask_id"])
                if kind in ("permission_asked", "permission_answered")
                else None
            ),
            request_id=(
                str(payload["request_id"])
                if kind in ("user_input_requested", "user_input_answered", "user_input_failed")
                else None
            ),
        )
        if sequence > error_acknowledged_through_sequence:
            if kind == "prompt":
                errored = False
            elif kind == "turn_ended":
                errored = payload.get("ending") == "failed"

    return ConversationAttentionSnapshot(
        conversation_id=conversation_id,
        latest_sequence=int(conversation["latest_sequence"]),
        owner_read_through_sequence=int(conversation["owner_read_through_sequence"]),
        error_acknowledged_through_sequence=error_acknowledged_through_sequence,
        latest_message_sequence=latest_message_sequence,
        pending_permission_ask_ids=pending_permission_ask_ids,
        pending_user_input_request_ids=pending_user_input_request_ids,
        errored=errored,
        occurred_at=occurred_at,
    )


def conversation_attention_snapshot_is_current(
    conn: sqlite3.Connection, snapshot: ConversationAttentionSnapshot
) -> bool:
    """Check only bounded markers after the conversation write lock is held."""

    row = conn.execute(
        "SELECT c.latest_sequence, c.owner_read_through_sequence, "
        "COALESCE(a.through_sequence, 0) AS error_acknowledged_through_sequence "
        "FROM conversations c LEFT JOIN conversation_error_acknowledgements a "
        "ON a.conversation_id=c.conversation_id WHERE c.conversation_id=?",
        (snapshot.conversation_id,),
    ).fetchone()
    return row is not None and (
        int(row["latest_sequence"]),
        int(row["owner_read_through_sequence"]),
        int(row["error_acknowledged_through_sequence"]),
    ) == (
        snapshot.latest_sequence,
        snapshot.owner_read_through_sequence,
        snapshot.error_acknowledged_through_sequence,
    )


def advance_conversation_attention(
    snapshot: ConversationAttentionSnapshot,
    payloads: tuple[ConversationEventPayload, ...],
    *,
    owner_read_through_sequence: int,
    occurred_at: int,
) -> ConversationAttention:
    """Apply one validated write batch without reading conversation events again."""

    latest_message_sequence = snapshot.latest_message_sequence
    pending_permission_ask_ids = snapshot.pending_permission_ask_ids
    pending_user_input_request_ids = snapshot.pending_user_input_request_ids
    errored = snapshot.errored
    for offset, payload in enumerate(payloads, start=1):
        sequence = snapshot.latest_sequence + offset
        if isinstance(payload, MessageToOwnerEventPayload):
            latest_message_sequence = sequence
        pending_permission_ask_ids, pending_user_input_request_ids = advance_pending_requests(
            pending_permission_ask_ids,
            pending_user_input_request_ids,
            payload.kind,
            ask_id=(
                payload.ask_id
                if isinstance(
                    payload, (PermissionAskedEventPayload, PermissionAnsweredEventPayload)
                )
                else None
            ),
            request_id=(
                payload.request_id
                if isinstance(
                    payload,
                    (
                        UserInputRequestedEventPayload,
                        UserInputAnsweredEventPayload,
                        UserInputFailedEventPayload,
                    ),
                )
                else None
            ),
        )
        if isinstance(payload, PromptEventPayload):
            errored = False
        elif isinstance(payload, TurnEndedEventPayload):
            errored = payload.ending is ConversationTurnEnding.failed

    return ConversationAttention(
        unread_message=latest_message_sequence > owner_read_through_sequence,
        pending_ask=bool(pending_permission_ask_ids or pending_user_input_request_ids),
        errored=errored,
        latest_sequence=snapshot.latest_sequence + len(payloads),
        occurred_at=max(snapshot.occurred_at, occurred_at),
    )


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
) -> dict[str, ConversationAttention]:
    where = "WHERE c.conversation_id = ? " if conversation_id is not None else ""
    parameters: tuple[str, ...] = (
        REQUEST_TERMINATING_EVENT_KIND,
        REQUEST_TERMINATING_EVENT_KIND,
    ) + ((conversation_id,) if conversation_id is not None else ())
    rows = conn.execute(
        "SELECT c.conversation_id, c.latest_sequence, "
        "COALESCE(MAX(CASE WHEN e.kind = 'message_to_owner' THEN e.sequence END), 0) "
        "> c.owner_read_through_sequence AS unread_message, "
        "EXISTS (SELECT 1 FROM conversation_events asked WHERE "
        "asked.conversation_id = c.conversation_id AND asked.kind = 'permission_asked' "
        "AND NOT EXISTS (SELECT 1 FROM conversation_events answered WHERE "
        "answered.conversation_id = c.conversation_id AND answered.kind = 'permission_answered' "
        "AND json_extract(answered.payload, '$.ask_id') = "
        "json_extract(asked.payload, '$.ask_id') AND answered.sequence > asked.sequence) "
        "AND NOT EXISTS (SELECT 1 FROM conversation_events ended WHERE "
        "ended.conversation_id = c.conversation_id AND ended.kind = ? "
        "AND ended.sequence > asked.sequence)) "
        "OR EXISTS (SELECT 1 FROM conversation_events asked WHERE "
        "asked.conversation_id = c.conversation_id AND asked.kind = 'user_input_requested' "
        "AND NOT EXISTS (SELECT 1 FROM conversation_events answered WHERE "
        "answered.conversation_id = c.conversation_id "
        "AND answered.kind IN ('user_input_answered','user_input_failed') "
        "AND json_extract(answered.payload, '$.request_id') = "
        "json_extract(asked.payload, '$.request_id') AND answered.sequence > asked.sequence) "
        "AND NOT EXISTS (SELECT 1 FROM conversation_events ended WHERE "
        "ended.conversation_id = c.conversation_id AND ended.kind = ? "
        "AND ended.sequence > asked.sequence)) "
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
        str(row["conversation_id"]): ConversationAttention(
            unread_message=bool(row["unread_message"]),
            pending_ask=bool(row["pending_ask"]),
            errored=bool(row["errored"]),
            latest_sequence=int(row["latest_sequence"]),
            occurred_at=int(row["occurred_at"]),
        )
        for row in rows
    }


def capture_ticket_attention(
    conn: sqlite3.Connection,
    ticket_id: str,
    occurred_at: int,
    *,
    conversation_result: ConversationAttention | None = None,
) -> None:
    # Local import keeps the conversation storage and work-attention projection acyclic.
    from planner.work_attention import ticket_assignment_from_values

    row = conn.execute(
        "SELECT id, stage, worker_type, worker_step_claim, pending_proposal, ceiling_holder, "
        f"conversation_id, {derivation.HAS_LIVE_BLOCKER_COLUMN} "
        "FROM tickets WHERE id=?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return
    facts = derivation.derive_ticket_facts(
        derivation.stored_facts_from_row(row, has_live_blocker=bool(row["has_live_blocker"]))
    )
    conversation = conversation_result
    if conversation is None:
        conversation_id = row["conversation_id"]
        conversation = (
            NOTHING_WAITING
            if conversation_id is None
            else conversation_attention(conn, conversation_id).get(
                str(conversation_id), NOTHING_WAITING
            )
        )
    holder = json.loads(str(row["ceiling_holder"]))
    owner_holds = holder.get("kind") == "owner"
    flags = {
        "awaiting_reply": conversation.unread_message,
        "awaiting_answer": conversation.pending_ask,
        "awaiting_approval": facts.ticket_status is TicketStatus.awaiting_approval and owner_holds,
        "assigned": ticket_assignment_from_values(
            stage=str(row["stage"]),
            worker_type=str(row["worker_type"]),
        ),
        "errored": facts.ticket_status is TicketStatus.errored or conversation.errored,
    }
    for notification_type, active in flags.items():
        _record(conn, "ticket", ticket_id, notification_type, active, occurred_at)


def capture_conversation_attention(
    conn: sqlite3.Connection,
    conversation_id: str,
    occurred_at: int,
    conversation_result: ConversationAttention | None = None,
) -> None:
    attention = conversation_result
    if attention is None:
        attention = conversation_attention(conn, conversation_id).get(conversation_id)
    if attention is None:
        return
    for row in conn.execute("SELECT id FROM tickets WHERE conversation_id=?", (conversation_id,)):
        capture_ticket_attention(
            conn,
            str(row["id"]),
            occurred_at,
            conversation_result=attention,
        )
    for row in conn.execute(
        "SELECT CASE WHEN i.id IS NULL THEN 'agent' ELSE 'manager' END subject_kind, "
        "COALESCE(i.id, a.agent_key) subject_id FROM agents a "
        "LEFT JOIN managers i ON a.agent_key=coalesce((SELECT agent_key FROM agents "
        "WHERE agent_key="
        "'sprint_item_supervisor_' || i.id),'manager_' || i.id) "
        "WHERE a.conversation_id=? AND (a.agent_key='chief_of_staff' OR i.id IS NOT NULL)",
        (conversation_id,),
    ):
        for notification_type, active in (
            ("awaiting_reply", attention.unread_message),
            ("awaiting_answer", attention.pending_ask),
            ("errored", attention.errored),
        ):
            _record(
                conn,
                str(row["subject_kind"]),
                str(row["subject_id"]),
                notification_type,
                active,
                occurred_at,
            )

    # Historical replay can publish attention before the goal schema exists.
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='goals'"
    ).fetchone() is not None:
        for row in conn.execute(
            "SELECT g.id FROM goals g JOIN agents a ON a.agent_key='goal_' || g.id "
            "WHERE a.conversation_id=?",
            (conversation_id,),
        ):
            for notification_type, active in (
                ("awaiting_reply", attention.unread_message),
                ("awaiting_answer", attention.pending_ask),
                ("errored", attention.errored),
            ):
                _record(conn, "goal", str(row[0]), notification_type, active, occurred_at)


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
