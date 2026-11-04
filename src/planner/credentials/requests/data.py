"""Metadata-only persistence and synchronous delivery receipt participation."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import cast

from planner.conversation.contracts import PromptDeliveryRefusalReason
from planner.conversation.events import (
    ConversationEventKind,
    ConversationEventPayload,
    CredentialRequestEventPayload,
    PromptDeliveryRefusedEventPayload,
    PromptDeliveryUncertainEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    conversation_event_payload_from_canonical_json,
)
from planner.core.contracts import ErrorCode, PlannerError
from planner.credentials.requests.contracts import CredentialRequestRowBody, RequestDeliveryStatus
from planner.credentials.requests.logic.validation import decode_snapshot


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read(conn: sqlite3.Connection, request_id: str) -> CredentialRequestRowBody:
    row = row_for(conn, request_id)
    return decode_snapshot(json.loads(row["body"]))


def row_for(conn: sqlite3.Connection, request_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM credential_requests WHERE request_id=?", (request_id,)
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "credential request not found")
    return cast(sqlite3.Row, row)


def update(
    conn: sqlite3.Connection, body: CredentialRequestRowBody, now: int
) -> CredentialRequestEventPayload:
    conn.execute(
        "UPDATE credential_requests SET body=?,updated_at=? WHERE request_id=?",
        (encoded(body), now, body["request_id"]),
    )
    return CredentialRequestEventPayload(body)


def delivery_status(
    payload: ConversationEventPayload,
) -> tuple[RequestDeliveryStatus, str | None] | None:
    if isinstance(payload, PromptEventPayload):
        return ("started", None)
    if isinstance(payload, PromptDeliveryUncertainEventPayload):
        return ("uncertain", None)
    if isinstance(payload, PromptDeliveryRefusedEventPayload):
        reason = payload.refusal_reason
        # The public compatibility fate calls a failed write refused. It does not prove
        # that the provider received no bytes. Never offer request retry on that basis.
        return (
            "uncertain"
            if reason == PromptDeliveryRefusalReason.write_to_backend_failed
            else "refused",
            str(reason),
        )
    if isinstance(payload, PromptDiscardedEventPayload):
        return ("canceled", None)
    return None


def settle_attempt(
    conn: sqlite3.Connection,
    attempt_id: str,
    status: RequestDeliveryStatus,
    reason: str | None,
    now: int,
) -> CredentialRequestEventPayload | None:
    attempt = conn.execute(
        "SELECT * FROM credential_request_delivery_attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone()
    if attempt is None:
        return None
    # A late queued HTTP response cannot overwrite a real prompt receipt.
    if status == "queued" and attempt["status"] != "intent":
        return None
    if (attempt["status"], attempt["refusal_reason"]) == (status, reason):
        return None
    conn.execute(
        "UPDATE credential_request_delivery_attempts SET status=?,refusal_reason=? "
        "WHERE attempt_id=?",
        (status, reason, attempt_id),
    )
    body = read(conn, attempt["request_id"])
    if body["delivery"] is None or body["delivery"]["attempt_id"] != attempt_id:
        return None
    body["delivery"] = {"attempt_id": attempt_id, "status": status, "refusal_reason": reason}
    return update(conn, body, now)


def _installed(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credential_requests'"
        ).fetchone()
        is not None
    )


def observe_events(
    conn: sqlite3.Connection, conversation_id: str, payloads: tuple[ConversationEventPayload, ...]
) -> tuple[ConversationEventPayload, ...]:
    """Settle the exact attempt and emit its card snapshot in the receipt transaction."""
    if not _installed(conn):
        return ()
    events: list[ConversationEventPayload] = []
    for payload in payloads:
        status = delivery_status(payload)
        identity = getattr(payload, "sender_message_id", None)
        if status is None or identity is None:
            continue
        attempt = conn.execute(
            "SELECT a.attempt_id FROM credential_request_delivery_attempts a "
            "JOIN credential_requests r ON r.request_id=a.request_id "
            "WHERE a.sender_message_id=? AND r.conversation_id=?",
            (identity, conversation_id),
        ).fetchone()
        if attempt is not None:
            event = settle_attempt(
                conn,
                attempt["attempt_id"],
                *status,
                now=int(conn.execute("SELECT unixepoch()").fetchone()[0]),
            )
            if event is not None:
                events.append(event)
    return tuple(events)


def reconcile_deliveries(
    conn: sqlite3.Connection,
) -> dict[str, tuple[ConversationEventPayload, ...]]:
    """Lost intent or runtime queue is unknown unless an exact durable receipt proves it."""
    if not _installed(conn):
        return {}
    grouped: dict[str, list[ConversationEventPayload]] = defaultdict(list)
    attempts = conn.execute(
        "SELECT a.*,r.conversation_id FROM credential_request_delivery_attempts a "
        "JOIN credential_requests r ON r.request_id=a.request_id "
        "WHERE a.status IN ('intent','queued')"
    ).fetchall()
    now = int(conn.execute("SELECT unixepoch()").fetchone()[0])
    for attempt in attempts:
        receipt = conn.execute(
            "SELECT kind,payload FROM conversation_events WHERE conversation_id=? "
            "AND kind IN ('prompt','prompt_delivery_refused',"
            "'prompt_delivery_uncertain','prompt_discarded') "
            "AND json_extract(payload,'$.sender_message_id')=? ORDER BY sequence DESC LIMIT 1",
            (attempt["conversation_id"], attempt["sender_message_id"]),
        ).fetchone()
        status = (
            None
            if receipt is None
            else delivery_status(
                conversation_event_payload_from_canonical_json(
                    ConversationEventKind(receipt["kind"]), receipt["payload"]
                )
            )
        )
        event = settle_attempt(
            conn, attempt["attempt_id"], *(status or ("uncertain", None)), now=now
        )
        if event is not None:
            grouped[attempt["conversation_id"]].append(event)
    return {key: tuple(value) for key, value in grouped.items()}
