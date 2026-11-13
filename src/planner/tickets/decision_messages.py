"""Decision instructions owned atomically by the same transaction as the decision."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import Ticket
from planner.tickets.logic.admission import validate_revision_guidance


def validate_request(message: str | None, revision: int | None, decision_id: str | None) -> None:
    if message is not None:
        validate_revision_guidance(message)
    if revision is not None and (type(revision) is not int or revision <= 0):
        raise PlannerError(
            ErrorCode.validation, "expected_proposal_revision must be a positive integer"
        )
    if decision_id is not None and (not decision_id.strip() or len(decision_id) > 200):
        raise PlannerError(ErrorCode.validation, "decision_id must contain 1 to 200 characters")


def request_json(principal: Principal, **values: Any) -> str:
    return json.dumps(
        {"sender_kind": principal.kind.value, "sender_id": principal.id, **values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def is_replay(conn: sqlite3.Connection, decision_id: str | None, request: str) -> bool:
    if decision_id is None:
        return False
    row = conn.execute(
        "SELECT request_json FROM ticket_decision_messages WHERE decision_id=?", (decision_id,)
    ).fetchone()
    if row is None:
        return False
    if str(row["request_json"]) != request:
        raise PlannerError(ErrorCode.validation, "decision_id already names a different decision")
    return True


def actor_name(conn: sqlite3.Connection, principal: Principal) -> str:
    if principal.kind is PrincipalKind.owner:
        return "User"
    if principal.kind is PrincipalKind.chief:
        return "Chief"
    if principal.kind is PrincipalKind.manager:
        row = conn.execute("SELECT title FROM managers WHERE id=?", (principal.id,)).fetchone()
        return f"Manager {row['title']}" if row else "Manager"
    return "Worker"


def save(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    action: str,
    field: str,
    principal: Principal,
    message: str | None,
    decision_id: str | None,
    request: str,
    now: int,
) -> None:
    identity = decision_id or f"legacy:{ticket.id}:{ticket.pending_proposal_revision}:{action}"
    field_label = next(
        (value.label for value in ticket.workflow.fields if value.id == field), field
    )
    text = f"{actor_name(conn, principal)} {action} the {field_label} proposal"
    text += f" with this note:\n{message}" if message else "."
    sender_message_id = "ticket_decision_" + hashlib.sha256(identity.encode()).hexdigest()
    conn.execute(
        "INSERT INTO ticket_decision_messages "
        "(decision_id,ticket_id,action,field,proposal_revision,sender_kind,sender_id,request_json,"
        "message,model_text,created_at,sender_message_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity,
            ticket.id,
            action,
            field,
            ticket.pending_proposal_revision,
            principal.kind.value,
            principal.id,
            request,
            message,
            text,
            now,
            sender_message_id,
        ),
    )


def pending_ticket_ids(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT ticket_id FROM ticket_decision_messages "
            "WHERE delivery_status IN ('pending','sending','queued')"
        )
    ]


def rearm_after_restart(conn: sqlite3.Connection, ticket_id: str) -> None:
    # Kill settled old queue ownership first. A real prompt wins over a lagging
    # outbox acknowledgement. Explicit restart authorizes retry of uncertain sends.
    rows = conn.execute(
        "SELECT decision_id,conversation_id,sender_message_id FROM "
        "ticket_decision_messages WHERE ticket_id=? AND delivery_status!='delivered'",
        (ticket_id,),
    ).fetchall()
    for row in rows:
        outcome = recorded_outcome(conn, row["conversation_id"], str(row["sender_message_id"]))
        conn.execute(
            "UPDATE ticket_decision_messages SET delivery_status=?,conversation_id=? "
            "WHERE decision_id=?",
            (
                "delivered" if outcome == "delivered" else "pending",
                row["conversation_id"] if outcome == "delivered" else None,
                row["decision_id"],
            ),
        )


def has_undelivered(conn: sqlite3.Connection, ticket_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM ticket_decision_messages WHERE ticket_id=? "
            "AND delivery_status!='delivered' LIMIT 1",
            (ticket_id,),
        ).fetchone()
        is not None
    )


def recorded_outcome(
    conn: sqlite3.Connection, conversation_id: str | None, sender_message_id: str
) -> str | None:
    row = conn.execute(
        "SELECT kind FROM conversation_events WHERE conversation_id=? "
        "AND json_extract(payload,'$.sender_message_id')=? "
        "AND kind IN ('prompt','prompt_delivery_refused',"
        "'prompt_delivery_uncertain','prompt_discarded') "
        "ORDER BY sequence DESC LIMIT 1",
        (conversation_id, sender_message_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "prompt": "delivered",
        "prompt_delivery_refused": "refused",
        "prompt_delivery_uncertain": "uncertain",
        "prompt_discarded": "discarded",
    }[str(row[0])]
