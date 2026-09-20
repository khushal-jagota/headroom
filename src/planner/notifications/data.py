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

from planner.core import ticket_blocks
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.notifications import attention as attention_data
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
from planner.tickets import derivation
from planner.tickets.contracts import TicketStatus
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


def _project_attention_facts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM notification_attention_edges WHERE "
        "(subject_kind = 'ticket' AND NOT EXISTS ("
        "SELECT 1 FROM tickets WHERE tickets.id = notification_attention_edges.subject_id)) "
        "OR (subject_kind = 'sprint_item' AND NOT EXISTS ("
        "SELECT 1 FROM sprint_items "
        "WHERE sprint_items.id = notification_attention_edges.subject_id)) "
        "OR (subject_kind = 'agent' AND NOT EXISTS ("
        "SELECT 1 FROM agents WHERE agents.agent_key = notification_attention_edges.subject_id))"
    )
    conn.execute(
        "DELETE FROM notification_attention_state WHERE "
        "(subject_kind = 'ticket' AND NOT EXISTS ("
        "SELECT 1 FROM tickets WHERE tickets.id = notification_attention_state.subject_id)) "
        "OR (subject_kind = 'sprint_item' AND NOT EXISTS ("
        "SELECT 1 FROM sprint_items "
        "WHERE sprint_items.id = notification_attention_state.subject_id)) "
        "OR (subject_kind = 'agent' AND NOT EXISTS ("
        "SELECT 1 FROM agents WHERE agents.agent_key = notification_attention_state.subject_id))"
    )
    conversations = attention_data.conversation_attention(conn)
    desired: dict[tuple[str, str, str], tuple[bool, Principal, str, int]] = {}
    ticket_rows = conn.execute(
        "SELECT id, title, stage, worker_type, worker_step_claim, pending_proposal, "
        "ceiling_holder, conversation_id, updated_at, worker_step_claim_changed_at FROM tickets"
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
        flags = {
            "awaiting_reply": conversation[0],
            "awaiting_approval": facts.proposal_is_parked and owner_holds,
            "assigned": ticket_assignment_from_values(
                stage=str(row["stage"]),
                worker_type=str(row["worker_type"]),
                owner_holds_ceiling=owner_holds,
            ),
            "errored": facts.ticket_status is TicketStatus.errored or conversation[1],
        }
        for notification_type, active in flags.items():
            occurred_at = (
                conversation[3]
                if notification_type in {"awaiting_reply", "errored"} and conversation[3]
                else int(row["worker_step_claim_changed_at"])
                if notification_type == "awaiting_approval"
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
    for row in conn.execute(
        "SELECT subject_kind, subject_id, notification_type, generation, occurred_at "
        "FROM notification_attention_edges WHERE projected=0 "
        "ORDER BY occurred_at, subject_kind, subject_id, "
        "notification_type, generation"
    ):
        key = (
            str(row["subject_kind"]),
            str(row["subject_id"]),
            str(row["notification_type"]),
        )
        current = desired.get(key)
        if current is None:
            continue
        _, subject, label, _ = current
        generation = int(row["generation"])
        _insert_fact(
            conn,
            fact_id=f"attention:{key[0]}:{key[1]}:{key[2]}:{generation}",
            notification_type=key[2],
            subject=subject,
            subject_label=label,
            source_kind="ticket" if subject.kind is PrincipalKind.ticket else "conversation",
            source_id=f"{key[0]}:{key[1]}:{key[2]}",
            source_sequence=generation,
            occurred_at=int(row["occurred_at"]),
        )
        conn.execute(
            "UPDATE notification_attention_edges SET projected=1 WHERE subject_kind=? "
            "AND subject_id=? AND notification_type=? AND generation=?",
            (*key, generation),
        )


def project_facts(conn: sqlite3.Connection) -> int:
    """Materialize one fact for each false-to-true attention transition."""
    inserted_before = conn.total_changes
    with _txn(conn):
        _project_attention_facts(conn)
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
