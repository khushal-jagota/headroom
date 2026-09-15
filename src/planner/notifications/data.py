"""SQLite record for facts, policy decisions, subscriptions, and delivery."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.notifications.contracts import (
    NOTIFICATION_SUBJECTS,
    NOTIFICATION_TYPE_BY_ID,
    SPRINT_ITEM_SUPERVISOR_NOTIFICATION_SUBJECT_KEY,
    TICKET_NOTIFICATION_SUBJECT_KEY,
    NotificationFact,
    NotificationIntent,
    PendingDelivery,
    PushSubscription,
    WebPushIdentity,
    notification_preference_is_valid,
)
from planner.notifications.logic.policy import decide_notification
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


def _insert_fact(
    conn: sqlite3.Connection,
    *,
    fact_id: str,
    notification_type: str,
    subject: Principal,
    subject_label: str,
    source_kind: str,
    source_id: str,
    source_sequence: int,
    occurred_at: int,
) -> None:
    stored_kind, stored_id = _stored_notification_subject(subject)
    payload = json.dumps(
        {"subject_label": subject_label},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    conn.execute(
        "INSERT OR IGNORE INTO notification_facts"
        "(fact_id, notification_type, subject_kind, ticket_id, agent_key, sprint_item_id, "
        "source_kind, source_id, source_sequence, occurred_at, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fact_id,
            notification_type,
            stored_kind,
            stored_id if stored_kind == "ticket" else None,
            stored_id if stored_kind == "agent" else None,
            stored_id if stored_kind == "sprint_item" else None,
            source_kind,
            source_id,
            source_sequence,
            occurred_at,
            payload,
        ),
    )


def _stored_notification_subject(subject: Principal) -> tuple[str, str]:
    """Translate a Principal to the unchanged notification table columns."""
    if subject.kind is PrincipalKind.ticket:
        return "ticket", subject.id
    if subject.kind is PrincipalKind.sprint_item:
        return "sprint_item", subject.id
    if subject.kind is PrincipalKind.chief:
        return "agent", CHIEF_SETTINGS_KEY
    raise ValueError(f"unsupported notification subject: {subject.kind.value}")


def _principal_from_stored_subject(subject_kind: str, subject_id: str) -> Principal:
    """Translate unchanged notification rows to the shared identity contract."""
    if subject_kind == "ticket":
        return Principal(PrincipalKind.ticket, subject_id)
    if subject_kind == "sprint_item":
        return Principal(PrincipalKind.sprint_item, subject_id)
    if subject_kind == "agent" and subject_id == CHIEF_SETTINGS_KEY:
        return CHIEF_PRINCIPAL
    raise ValueError(f"unknown notification subject: {subject_kind}/{subject_id}")


def _preference_subject_key(fact: NotificationFact) -> str:
    """Which saved switch decides this fact.

    Every Ticket shares one switch, and every Sprint Item shares one: a sprint holds
    twenty or thirty Items and they are replaced each sprint, so a switch per Item would
    be a screen of rows that die. An agent is its own subject, because there is one of
    each.
    """
    if fact.subject.kind is PrincipalKind.ticket:
        return TICKET_NOTIFICATION_SUBJECT_KEY
    if fact.subject.kind is PrincipalKind.sprint_item:
        return SPRINT_ITEM_SUPERVISOR_NOTIFICATION_SUBJECT_KEY
    if fact.subject.kind is PrincipalKind.chief:
        return CHIEF_SETTINGS_KEY
    raise ValueError(f"unsupported notification subject: {fact.subject.kind.value}")


def _ticket_fact_type(status: str) -> str | None:
    return {
        "awaiting_approval": "ticket_needs_approval",
        "needs_user": "needs_input",
        "errored": "worker_failed",
    }.get(status)


def _agent_label(agent_key: str) -> str:
    if agent_key == CHIEF_SETTINGS_KEY:
        return CHIEF_LABEL
    return agent_key.replace("_", " ").title()


def project_facts(conn: sqlite3.Connection) -> int:
    """Advance source cursors and materialize new normalized facts once."""
    inserted_before = conn.total_changes
    with _txn(conn):
        ticket_rows = conn.execute(
            "SELECT t.id, t.title, t.ticket_status, t.ticket_status_revision, "
            "t.ticket_status_changed_at, c.sequence AS projected_sequence "
            "FROM tickets t LEFT JOIN notification_projection_cursors c "
            "ON c.source_kind = 'ticket' AND c.source_id = t.id "
            "WHERE c.source_id IS NULL OR t.ticket_status_revision > c.sequence"
        ).fetchall()
        for row in ticket_rows:
            revision = int(row["ticket_status_revision"])
            notification_type = _ticket_fact_type(str(row["ticket_status"]))
            if notification_type is not None and revision > 0:
                _insert_fact(
                    conn,
                    fact_id=f"ticket:{row['id']}:{revision}",
                    notification_type=notification_type,
                    subject=Principal(PrincipalKind.ticket, str(row["id"])),
                    subject_label=str(row["title"]),
                    source_kind="ticket",
                    source_id=str(row["id"]),
                    source_sequence=revision,
                    occurred_at=int(row["ticket_status_changed_at"]),
                )
            conn.execute(
                "INSERT INTO notification_projection_cursors(source_kind, source_id, sequence) "
                "VALUES ('ticket', ?, ?) ON CONFLICT(source_kind, source_id) DO UPDATE SET "
                "sequence = excluded.sequence",
                (str(row["id"]), revision),
            )

        # A Sprint Item supervisor is an agent, but the reader knows it as its Item: the
        # push says the Item's title and opens the Item. So its conversation's facts take
        # the Item as their subject, not the agent.
        conversations = conn.execute(
            "SELECT c.conversation_id, c.latest_sequence, "
            "CASE WHEN t.id IS NOT NULL THEN 'ticket' "
            "WHEN i.id IS NOT NULL THEN 'sprint_item' ELSE 'agent' END AS subject_kind, "
            "COALESCE(t.id, i.id, a.agent_key) AS subject_id, "
            "COALESCE(t.title, i.title) AS subject_title, "
            "pc.sequence AS projected_sequence "
            "FROM conversations c "
            "LEFT JOIN tickets t ON t.conversation_id = c.conversation_id "
            "LEFT JOIN agents a ON a.conversation_id = c.conversation_id "
            "LEFT JOIN sprint_items i ON i.supervisor_agent_key = a.agent_key "
            "LEFT JOIN notification_projection_cursors pc "
            "ON pc.source_kind = 'conversation' AND pc.source_id = c.conversation_id "
            "WHERE (t.id IS NOT NULL OR i.id IS NOT NULL OR a.agent_key = ?) "
            "AND (pc.source_id IS NULL OR c.latest_sequence > pc.sequence)",
            (CHIEF_SETTINGS_KEY,),
        ).fetchall()
        for conversation in conversations:
            conversation_id = str(conversation["conversation_id"])
            stored_subject_kind = str(conversation["subject_kind"])
            subject_id = str(conversation["subject_id"])
            try:
                subject = _principal_from_stored_subject(stored_subject_kind, subject_id)
            except ValueError:
                continue
            subject_label = (
                str(conversation["subject_title"])
                if subject.kind in {PrincipalKind.ticket, PrincipalKind.sprint_item}
                else _agent_label(subject_id)
            )
            after = (
                int(conversation["projected_sequence"])
                if conversation["projected_sequence"] is not None
                else 0
            )
            events = conn.execute(
                "SELECT sequence, kind, payload, created_at FROM conversation_events "
                "WHERE conversation_id = ? AND sequence > ? ORDER BY sequence",
                (conversation_id, after),
            ).fetchall()
            for event in events:
                kind = str(event["kind"])
                payload = json.loads(str(event["payload"]))
                event_notification_type: str | None = None
                if kind == "permission_asked":
                    event_notification_type = "permission_requested"
                elif kind == "user_input_requested":
                    event_notification_type = "needs_input"
                elif (
                    kind == "turn_ended"
                    and payload.get("ending") == "completed"
                    and payload.get("automatic_compaction_result") != "not_compacted"
                ):
                    event_notification_type = "worker_completed"
                elif kind == "turn_ended" and payload.get("ending") == "failed":
                    event_notification_type = "worker_failed"
                if event_notification_type is not None:
                    sequence = int(event["sequence"])
                    _insert_fact(
                        conn,
                        fact_id=f"conversation:{conversation_id}:{sequence}",
                        notification_type=event_notification_type,
                        subject=subject,
                        subject_label=subject_label,
                        source_kind="conversation",
                        source_id=conversation_id,
                        source_sequence=sequence,
                        occurred_at=int(event["created_at"]),
                    )
            conn.execute(
                "INSERT INTO notification_projection_cursors(source_kind, source_id, sequence) "
                "VALUES ('conversation', ?, ?) "
                "ON CONFLICT(source_kind, source_id) DO UPDATE SET sequence = excluded.sequence",
                (conversation_id, int(conversation["latest_sequence"])),
            )

    return conn.total_changes - inserted_before


def apply_policy(conn: sqlite3.Connection, now: int) -> int:
    """Decide every undecided fact through the one policy door."""
    decisions = 0
    with _txn(conn):
        preferences = resolved_preferences(conn)
        rows = conn.execute(
            "SELECT f.fact_id, f.notification_type, f.subject_kind, "
            "COALESCE(f.ticket_id, f.agent_key, f.sprint_item_id) AS subject_id, "
            "f.occurred_at, f.payload "
            "FROM notification_facts f LEFT JOIN notification_decisions d "
            "ON d.fact_id = f.fact_id WHERE d.fact_id IS NULL "
            "ORDER BY f.occurred_at, f.fact_id"
        ).fetchall()
        subscriptions = active_subscription_ids(conn)
        for row in rows:
            payload = json.loads(str(row["payload"]))
            fact_id = str(row["fact_id"])
            try:
                subject = _principal_from_stored_subject(
                    str(row["subject_kind"]), str(row["subject_id"])
                )
            except ValueError:
                # Old arbitrary-agent facts have no Principal in the closed vocabulary.
                # They never matched a saved preference, so preserve that suppression.
                conn.execute(
                    "INSERT INTO notification_decisions(fact_id, outcome, decided_at) "
                    "VALUES (?, 'suppress', ?)",
                    (fact_id, now),
                )
                decisions += 1
                continue
            fact = NotificationFact(
                fact_id=fact_id,
                notification_type=str(row["notification_type"]),
                subject=subject,
                # Facts copied by notification_subjects retain their original payload
                # so an undecided pre-upgrade fact remains usable without rewriting history.
                subject_label=str(
                    payload.get("subject_label", payload.get("ticket_title", "Panels"))
                ),
                occurred_at=int(row["occurred_at"]),
            )
            subject_key = _preference_subject_key(fact)
            intent = decide_notification(
                fact,
                enabled=preferences.get((subject_key, fact.notification_type), False),
            )
            outcome = "notify" if intent is not None else "suppress"
            conn.execute(
                "INSERT INTO notification_decisions(fact_id, outcome, decided_at) VALUES (?, ?, ?)",
                (fact.fact_id, outcome, now),
            )
            if intent is not None:
                conn.execute(
                    "INSERT INTO notification_intents"
                    "(fact_id, title, body, route, tag, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        intent.fact_id,
                        intent.title,
                        intent.body,
                        intent.route,
                        intent.tag,
                        now,
                    ),
                )
                for subscription_id in subscriptions:
                    conn.execute(
                        "INSERT INTO notification_deliveries"
                        "(fact_id, subscription_id, status, attempts, next_attempt_at) "
                        "VALUES (?, ?, 'pending', 0, ?)",
                        (intent.fact_id, subscription_id, now),
                    )
            decisions += 1
    return decisions


def pending_deliveries(
    conn: sqlite3.Connection, now: int, *, limit: int = 50
) -> tuple[PendingDelivery, ...]:
    rows = conn.execute(
        "SELECT d.fact_id, d.attempts, s.subscription_id, s.endpoint, s.p256dh, s.auth, "
        "i.title, i.body, i.route, i.tag FROM notification_deliveries d "
        "JOIN notification_push_subscriptions s ON s.subscription_id = d.subscription_id "
        "JOIN notification_intents i ON i.fact_id = d.fact_id "
        "WHERE d.status IN ('pending','retry') AND d.next_attempt_at <= ? "
        "AND s.disabled_at IS NULL ORDER BY d.next_attempt_at, d.fact_id LIMIT ?",
        (now, limit),
    ).fetchall()
    return tuple(
        PendingDelivery(
            fact_id=str(row["fact_id"]),
            attempts=int(row["attempts"]),
            subscription=PushSubscription(
                str(row["subscription_id"]),
                str(row["endpoint"]),
                str(row["p256dh"]),
                str(row["auth"]),
            ),
            intent=NotificationIntent(
                fact_id=str(row["fact_id"]),
                title=str(row["title"]),
                body=str(row["body"]),
                route=str(row["route"]),
                tag=str(row["tag"]),
            ),
        )
        for row in rows
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
            "last_error = NULL, delivered_at = ? WHERE fact_id = ? AND subscription_id = ?",
            (attempts, now, delivery.fact_id, delivery.subscription.subscription_id),
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
                "last_error = ? WHERE fact_id = ? AND subscription_id = ?",
                (
                    attempts,
                    error,
                    delivery.fact_id,
                    delivery.subscription.subscription_id,
                ),
            )
        return
    # 30s, 60s, 120s... capped at one hour.
    next_attempt_at = now + min(3600, 30 * (2 ** min(attempts - 1, 7)))
    conn.execute(
        "UPDATE notification_deliveries SET status = 'retry', attempts = ?, "
        "next_attempt_at = ?, last_error = ? WHERE fact_id = ? AND subscription_id = ?",
        (
            attempts,
            next_attempt_at,
            error,
            delivery.fact_id,
            delivery.subscription.subscription_id,
        ),
    )
