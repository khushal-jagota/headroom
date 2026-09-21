"""SQLite record for attention edges, subscriptions, and delivery."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from planner.core import ticket_blocks
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.notifications import attention as attention_data
from planner.notifications.contracts import (
    NOTIFICATION_SUBJECTS,
    NOTIFICATION_TYPE_BY_ID,
    SPRINT_ITEM_SUPERVISOR_NOTIFICATION_SUBJECT_KEY,
    TICKET_NOTIFICATION_SUBJECT_KEY,
    AttentionEdge,
    EdgeKey,
    NotificationIntent,
    PendingDelivery,
    PushSubscription,
    WebPushIdentity,
    notification_preference_is_valid,
)
from planner.notifications.logic.policy import decide_notification
from planner.tickets import derivation
from planner.tickets.contracts import TicketStatus
from planner.tickets.logic import fields_codec
from planner.work_attention import ticket_assignment_from_values
from planner.worker_settings.service import CHIEF_LABEL, CHIEF_SETTINGS_KEY


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    changes_before = conn.total_changes
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        # A read-only reconciliation pass is not a change and must not wake itself
        # (or every browser) through the process-wide commit signal.
        conn.execute("COMMIT" if conn.total_changes != changes_before else "ROLLBACK")


def resolved_preferences(conn: sqlite3.Connection) -> dict[tuple[str, str], bool]:
    stored = {
        (str(row["subject_key"]), str(row["notification_type"])): bool(row["enabled"])
        for row in conn.execute(
            "SELECT subject_key, notification_type, enabled FROM notification_preferences"
        )
    }
    return {
        (subject.key, notification_type): stored.get(
            (subject.key, notification_type),
            NOTIFICATION_TYPE_BY_ID[notification_type].default_enabled,
        )
        for subject in NOTIFICATION_SUBJECTS
        for notification_type in subject.notification_type_ids
    }


def set_preference(
    conn: sqlite3.Connection,
    subject_key: str,
    notification_type: str,
    enabled: bool,
    now: int,
) -> None:
    if not notification_preference_is_valid(subject_key, notification_type):
        raise ValueError(f"unknown notification preference: {subject_key}/{notification_type}")
    conn.execute(
        "INSERT INTO notification_preferences"
        "(subject_key, notification_type, enabled, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(subject_key, notification_type) DO UPDATE SET "
        "enabled = excluded.enabled, updated_at = excluded.updated_at",
        (subject_key, notification_type, int(enabled), now),
    )


# The one preference this build ships turned off. Everything else is on until the owner
# says otherwise, so only the exception is stored.
SHIPPED_DISABLED_PREFERENCES: Final = (
    (SPRINT_ITEM_SUPERVISOR_NOTIFICATION_SUBJECT_KEY, "errored"),
)


def seed_shipped_notification_preferences(conn: sqlite3.Connection) -> None:
    """Put the shipped preferences in an empty table, and nothing in a populated one."""
    if conn.execute("SELECT 1 FROM notification_preferences LIMIT 1").fetchone() is not None:
        return
    for subject_key, notification_type in SHIPPED_DISABLED_PREFERENCES:
        conn.execute(
            "INSERT INTO notification_preferences"
            "(subject_key, notification_type, enabled, updated_at) VALUES (?, ?, 0, 0)",
            (subject_key, notification_type),
        )


def get_or_create_web_push_identity(conn: sqlite3.Connection, now: int) -> WebPushIdentity:
    row = conn.execute(
        "SELECT private_key, public_key FROM notification_web_push_identity WHERE singleton = 1"
    ).fetchone()
    if row is not None:
        return WebPushIdentity(str(row["private_key"]), str(row["public_key"]))
    private = ec.generate_private_key(ec.SECP256R1())
    private_der = private.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    candidate = WebPushIdentity(_b64url(private_der), _b64url(public))
    with _txn(conn):
        conn.execute(
            "INSERT OR IGNORE INTO notification_web_push_identity"
            "(singleton, private_key, public_key, created_at) VALUES (1, ?, ?, ?)",
            (candidate.private_key, candidate.public_key, now),
        )
    row = conn.execute(
        "SELECT private_key, public_key FROM notification_web_push_identity WHERE singleton = 1"
    ).fetchone()
    assert row is not None
    return WebPushIdentity(str(row["private_key"]), str(row["public_key"]))


def read_web_push_identity(conn: sqlite3.Connection) -> WebPushIdentity:
    row = conn.execute(
        "SELECT private_key, public_key FROM notification_web_push_identity WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("Web Push identity was not initialized")
    return WebPushIdentity(str(row["private_key"]), str(row["public_key"]))


def register_subscription(
    conn: sqlite3.Connection,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    now: int,
) -> PushSubscription:
    subscription_id = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:24]
    conn.execute(
        "INSERT INTO notification_push_subscriptions"
        "(subscription_id, endpoint, p256dh, auth, created_at, updated_at, disabled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL) ON CONFLICT(endpoint) DO UPDATE SET "
        "p256dh = excluded.p256dh, auth = excluded.auth, updated_at = excluded.updated_at, "
        "disabled_at = NULL",
        (subscription_id, endpoint, p256dh, auth, now, now),
    )
    row = conn.execute(
        "SELECT subscription_id, endpoint, p256dh, auth "
        "FROM notification_push_subscriptions WHERE endpoint = ?",
        (endpoint,),
    ).fetchone()
    assert row is not None
    return PushSubscription(
        str(row["subscription_id"]),
        str(row["endpoint"]),
        str(row["p256dh"]),
        str(row["auth"]),
    )


def remove_subscription(conn: sqlite3.Connection, subscription_id: str) -> bool:
    cursor = conn.execute(
        "DELETE FROM notification_push_subscriptions WHERE subscription_id = ?",
        (subscription_id,),
    )
    return cursor.rowcount > 0


def active_subscription_ids(conn: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row["subscription_id"])
        for row in conn.execute(
            "SELECT subscription_id FROM notification_push_subscriptions "
            "WHERE disabled_at IS NULL ORDER BY created_at, subscription_id"
        )
    )


def _principal_from_stored_subject(subject_kind: str, subject_id: str) -> Principal:
    """Translate unchanged notification rows to the shared identity contract."""
    if subject_kind == "ticket":
        return Principal(PrincipalKind.ticket, subject_id)
    if subject_kind == "sprint_item":
        return Principal(PrincipalKind.sprint_item, subject_id)
    if subject_kind == "agent" and subject_id == CHIEF_SETTINGS_KEY:
        return CHIEF_PRINCIPAL
    raise ValueError(f"unknown notification subject: {subject_kind}/{subject_id}")


def _preference_subject_key(edge: AttentionEdge) -> str:
    """Which saved switch decides this edge.

    Every Ticket shares one switch, and every Sprint Item shares one: a sprint holds
    twenty or thirty Items and they are replaced each sprint, so a switch per Item would
    be a screen of rows that die. An agent is its own subject, because there is one of
    each.
    """
    if edge.subject.kind is PrincipalKind.ticket:
        return TICKET_NOTIFICATION_SUBJECT_KEY
    if edge.subject.kind is PrincipalKind.sprint_item:
        return SPRINT_ITEM_SUPERVISOR_NOTIFICATION_SUBJECT_KEY
    if edge.subject.kind is PrincipalKind.chief:
        return CHIEF_SETTINGS_KEY
    raise ValueError(f"unsupported notification subject: {edge.subject.kind.value}")


def _owner_holds_ticket_ceiling(raw_holder: str) -> bool:
    stored = json.loads(raw_holder)
    if not isinstance(stored, dict):
        return False
    try:
        holder = Principal(PrincipalKind(str(stored["kind"])), str(stored["id"]))
    except (KeyError, TypeError, ValueError):
        return False
    return holder == OWNER_PRINCIPAL


def _agent_label(agent_key: str) -> str:
    if agent_key == CHIEF_SETTINGS_KEY:
        return CHIEF_LABEL
    return agent_key.replace("_", " ").title()


_SUBJECT_TABLES = (
    "notification_attention_edges",
    "notification_attention_state",
    "notification_deliveries",
)


def _prune_missing_subjects(conn: sqlite3.Connection) -> None:
    """Nothing survives its subject. The row used to go by foreign key cascade."""
    for table in _SUBJECT_TABLES:
        conn.execute(
            f"DELETE FROM {table} WHERE "
            "(subject_kind = 'ticket' AND NOT EXISTS ("
            f"SELECT 1 FROM tickets WHERE tickets.id = {table}.subject_id)) "
            "OR (subject_kind = 'sprint_item' AND NOT EXISTS ("
            "SELECT 1 FROM sprint_items "
            f"WHERE sprint_items.id = {table}.subject_id)) "
            "OR (subject_kind = 'agent' AND NOT EXISTS ("
            f"SELECT 1 FROM agents WHERE agents.agent_key = {table}.subject_id))"
        )


def _queue_attention_deliveries(conn: sqlite3.Connection, now: int) -> int:
    _prune_missing_subjects(conn)
    conversations = attention_data.conversation_attention(conn)
    desired: dict[tuple[str, str, str], tuple[bool, Principal, str, int]] = {}
    ticket_rows = conn.execute(
        "SELECT id, title, stage, worker_type, worker_step_claim, pending_proposal, "
        "ceiling_holder, conversation_id, updated_at FROM tickets"
    ).fetchall()
    blocked_ticket_ids = ticket_blocks.blocked_ticket_ids(conn)
    for row in ticket_rows:
        facts = derivation.derive_ticket_facts(
            derivation.stored_facts_from_row(
                row, has_live_blocker=str(row["id"]) in blocked_ticket_ids
            )
        )
        ticket_id = str(row["id"])
        subject = Principal(PrincipalKind.ticket, ticket_id)
        label = str(row["title"])
        conversation = conversations.get(str(row["conversation_id"]), (False, False, 0, 0))
        owner_holds = _owner_holds_ticket_ceiling(str(row["ceiling_holder"]))
        parked = fields_codec.proposal_from_json(row["pending_proposal"])
        flags = {
            "awaiting_reply": conversation[0],
            "awaiting_approval": (
                facts.ticket_status is TicketStatus.awaiting_approval and owner_holds
            ),
            "assigned": ticket_assignment_from_values(
                stage=str(row["stage"]),
                worker_type=str(row["worker_type"]),
            ),
            "errored": facts.ticket_status is TicketStatus.errored or conversation[1],
        }
        for notification_type, active in flags.items():
            occurred_at = (
                conversation[3]
                if notification_type in {"awaiting_reply", "errored"} and conversation[3]
                # A parked proposal knows when it was parked, and that is the wait.
                # It is the same number the review list shows.
                else int(parked.created_at)
                if notification_type == "awaiting_approval" and parked is not None
                else int(row["updated_at"])
            )
            desired[("ticket", ticket_id, notification_type)] = (
                active,
                subject,
                label,
                occurred_at,
            )

    agent_rows = conn.execute(
        "SELECT a.agent_key, a.conversation_id, i.id AS item_id, i.title AS item_title "
        "FROM agents a LEFT JOIN sprint_items i ON i.supervisor_agent_key = a.agent_key "
        "WHERE a.agent_key = ? OR i.id IS NOT NULL",
        (CHIEF_SETTINGS_KEY,),
    ).fetchall()
    for row in agent_rows:
        is_item = row["item_id"] is not None
        subject_kind = "sprint_item" if is_item else "agent"
        subject_id = str(row["item_id"] if is_item else row["agent_key"])
        subject = _principal_from_stored_subject(subject_kind, subject_id)
        label = str(row["item_title"]) if is_item else _agent_label(subject_id)
        conversation = conversations.get(str(row["conversation_id"]), (False, False, 0, 0))
        for notification_type, active in (
            ("awaiting_reply", conversation[0]),
            ("errored", conversation[1]),
        ):
            desired[(subject_kind, subject_id, notification_type)] = (
                active,
                subject,
                label,
                conversation[3],
            )

    attention_data.reconcile_attention(
        conn,
        {key: (active, occurred_at) for key, (active, _, _, occurred_at) in desired.items()},
    )
    preferences = resolved_preferences(conn)
    subscriptions = active_subscription_ids(conn)
    decided = 0
    for row in conn.execute(
        "SELECT subject_kind, subject_id, notification_type, generation, occurred_at "
        "FROM notification_attention_edges WHERE decided=0 "
        "ORDER BY occurred_at, subject_kind, subject_id, "
        "notification_type, generation"
    ).fetchall():
        subject_key = (
            str(row["subject_kind"]),
            str(row["subject_id"]),
            str(row["notification_type"]),
        )
        current = desired.get(subject_key)
        if current is None:
            continue
        _, subject, label, _ = current
        key = EdgeKey(*subject_key, int(row["generation"]))
        edge = AttentionEdge(
            key=key,
            subject=subject,
            subject_label=label,
            occurred_at=int(row["occurred_at"]),
        )
        intent = decide_notification(
            edge,
            enabled=preferences.get(
                (_preference_subject_key(edge), edge.notification_type), False
            ),
        )
        if intent is not None:
            for subscription_id in subscriptions:
                conn.execute(
                    "INSERT OR IGNORE INTO notification_deliveries"
                    "(subject_kind, subject_id, notification_type, generation, subscription_id, "
                    "title, body, route, tag, created_at, status, attempts, next_attempt_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)",
                    (
                        key.subject_kind,
                        key.subject_id,
                        key.notification_type,
                        key.generation,
                        subscription_id,
                        intent.title,
                        intent.body,
                        intent.route,
                        intent.tag,
                        now,
                        now,
                    ),
                )
        # A suppressed edge is decided too, and leaves nothing behind.
        conn.execute(
            "UPDATE notification_attention_edges SET decided=1 WHERE subject_kind=? "
            "AND subject_id=? AND notification_type=? AND generation=?",
            (*subject_key, key.generation),
        )
        decided += 1
    return decided


def queue_deliveries(conn: sqlite3.Connection, now: int) -> int:
    """Turn every undecided rising edge into the deliveries it earns.

    One step. The saved preference decides the edge, the one policy door writes the
    words, and an enabled edge becomes one delivery row per registered device.
    """
    with _txn(conn):
        return _queue_attention_deliveries(conn, now)


_SUBJECT_STILL_EXISTS = (
    "((d.subject_kind = 'ticket' "
    "AND EXISTS (SELECT 1 FROM tickets WHERE tickets.id = d.subject_id)) "
    "OR (d.subject_kind = 'sprint_item' "
    "AND EXISTS (SELECT 1 FROM sprint_items WHERE sprint_items.id = d.subject_id)) "
    "OR (d.subject_kind = 'agent' "
    "AND EXISTS (SELECT 1 FROM agents WHERE agents.agent_key = d.subject_id)))"
)


def pending_deliveries(
    conn: sqlite3.Connection, now: int, *, limit: int = 50
) -> tuple[PendingDelivery, ...]:
    rows = conn.execute(
        "SELECT d.subject_kind, d.subject_id, d.notification_type, d.generation, d.attempts, "
        "d.title, d.body, d.route, d.tag, "
        "s.subscription_id, s.endpoint, s.p256dh, s.auth FROM notification_deliveries d "
        "JOIN notification_push_subscriptions s ON s.subscription_id = d.subscription_id "
        "WHERE d.status IN ('pending','retry') AND d.next_attempt_at <= ? "
        "AND s.disabled_at IS NULL "
        # The log holds no foreign key to its subject, so a Ticket deleted after the
        # queue step's prune would otherwise still be pushed about.
        f"AND {_SUBJECT_STILL_EXISTS} "
        "ORDER BY d.next_attempt_at, d.subject_kind, d.subject_id, "
        "d.notification_type, d.generation LIMIT ?",
        (now, limit),
    ).fetchall()
    return tuple(
        PendingDelivery(
            edge=EdgeKey(
                str(row["subject_kind"]),
                str(row["subject_id"]),
                str(row["notification_type"]),
                int(row["generation"]),
            ),
            attempts=int(row["attempts"]),
            subscription=PushSubscription(
                str(row["subscription_id"]),
                str(row["endpoint"]),
                str(row["p256dh"]),
                str(row["auth"]),
            ),
            intent=NotificationIntent(
                title=str(row["title"]),
                body=str(row["body"]),
                route=str(row["route"]),
                tag=str(row["tag"]),
            ),
        )
        for row in rows
    )


def _delivery_row(delivery: PendingDelivery) -> tuple[str, str, str, int, str]:
    return (
        delivery.edge.subject_kind,
        delivery.edge.subject_id,
        delivery.edge.notification_type,
        delivery.edge.generation,
        delivery.subscription.subscription_id,
    )


_DELIVERY_ROW_WHERE = (
    " WHERE subject_kind = ? AND subject_id = ? AND notification_type = ? "
    "AND generation = ? AND subscription_id = ?"
)


def record_delivery_result(
    conn: sqlite3.Connection,
    delivery: PendingDelivery,
    *,
    now: int,
    delivered: bool,
    expired: bool,
    error: str | None,
) -> None:
    attempts = delivery.attempts + 1
    if delivered:
        conn.execute(
            "UPDATE notification_deliveries SET status = 'delivered', attempts = ?, "
            "last_error = NULL, delivered_at = ?" + _DELIVERY_ROW_WHERE,
            (attempts, now, *_delivery_row(delivery)),
        )
        return
    if expired:
        with _txn(conn):
            conn.execute(
                "UPDATE notification_push_subscriptions SET disabled_at = ? "
                "WHERE subscription_id = ?",
                (now, delivery.subscription.subscription_id),
            )
            conn.execute(
                "UPDATE notification_deliveries SET status = 'expired', attempts = ?, "
                "last_error = ?" + _DELIVERY_ROW_WHERE,
                (attempts, error, *_delivery_row(delivery)),
            )
        return
    # 30s, 60s, 120s... capped at one hour.
    next_attempt_at = now + min(3600, 30 * (2 ** min(attempts - 1, 7)))
    conn.execute(
        "UPDATE notification_deliveries SET status = 'retry', attempts = ?, "
        "next_attempt_at = ?, last_error = ?" + _DELIVERY_ROW_WHERE,
        (attempts, next_attempt_at, error, *_delivery_row(delivery)),
    )
