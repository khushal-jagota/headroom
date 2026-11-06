"""Durable native registrations and delivery records, separate from Web Push."""

from __future__ import annotations

import sqlite3
from typing import Literal, cast
from uuid import uuid4

from planner.core.contracts import OWNER_PRINCIPAL_ID
from planner.notifications.config import (
    APNS_BATCH_SIZE,
    APNS_DELIVERY_TTL_SECONDS,
    APNS_MAX_ATTEMPTS,
    APNS_RETRY_BASE_SECONDS,
)
from planner.notifications.contracts import (
    APNsDevice,
    APNsResult,
    EdgeKey,
    NotificationIntent,
    PendingAPNsDelivery,
)


def register_device(
    conn: sqlite3.Connection,
    *,
    device_token: str,
    bundle_id: str,
    environment: Literal["development", "production"],
    now: int,
) -> None:
    conn.execute(
        "INSERT INTO notification_apns_devices "
        "(device_id,owner_id,device_token,bundle_id,environment,registration_version,"
        "created_at,updated_at) VALUES (?,?,?,?,?,1,?,?) "
        "ON CONFLICT(owner_id,device_token,bundle_id,environment) DO UPDATE SET "
        "updated_at=excluded.updated_at,registration_version=registration_version+1",
        (uuid4().hex, OWNER_PRINCIPAL_ID, device_token, bundle_id, environment, now, now),
    )


def remove_device(
    conn: sqlite3.Connection,
    *,
    device_token: str,
    bundle_id: str,
    environment: Literal["development", "production"],
) -> bool:
    return (
        conn.execute(
            "DELETE FROM notification_apns_devices WHERE owner_id=? AND device_token=? "
            "AND bundle_id=? AND environment=?",
            (OWNER_PRINCIPAL_ID, device_token, bundle_id, environment),
        ).rowcount
        > 0
    )


def queue_intent(
    conn: sqlite3.Connection,
    edge: EdgeKey,
    intent: NotificationIntent,
    now: int,
) -> None:
    """Called inside the attention decision transaction, before its decided mark."""
    conn.execute(
        "INSERT OR IGNORE INTO notification_apns_deliveries "
        "(subject_kind,subject_id,notification_type,generation,device_id,"
        "title,body,route,tag,created_at,status,attempts,next_attempt_at) "
        "SELECT ?,?,?,?,device_id,?,?,?,?,?,'pending',0,? "
        "FROM notification_apns_devices WHERE owner_id=?",
        (
            edge.subject_kind,
            edge.subject_id,
            edge.notification_type,
            edge.generation,
            intent.title,
            intent.body,
            intent.route,
            intent.tag,
            now,
            now,
            OWNER_PRINCIPAL_ID,
        ),
    )


def pending_deliveries(conn: sqlite3.Connection, now: int) -> tuple[PendingAPNsDelivery, ...]:
    conn.execute(
        "UPDATE notification_apns_deliveries SET status='expired' "
        "WHERE status IN ('pending','retry') AND created_at <= ?",
        (now - APNS_DELIVERY_TTL_SECONDS,),
    )
    rows = conn.execute(
        "SELECT d.*,s.device_token,s.bundle_id,s.environment,s.registration_version,s.updated_at "
        "FROM notification_apns_deliveries d JOIN notification_apns_devices s USING(device_id) "
        "WHERE d.status IN ('pending','retry') AND d.next_attempt_at <= ? "
        "AND ((d.subject_kind='ticket' AND EXISTS "
        "(SELECT 1 FROM tickets WHERE id=d.subject_id)) OR "
        "(d.subject_kind='manager' AND EXISTS (SELECT 1 FROM managers WHERE id=d.subject_id)) OR "
        "(d.subject_kind='agent' AND EXISTS (SELECT 1 FROM agents WHERE agent_key=d.subject_id))) "
        "ORDER BY d.next_attempt_at,d.device_id LIMIT ?",
        (now, APNS_BATCH_SIZE),
    ).fetchall()
    return tuple(
        PendingAPNsDelivery(
            EdgeKey(
                row["subject_kind"], row["subject_id"], row["notification_type"], row["generation"]
            ),
            APNsDevice(
                row["device_id"],
                row["device_token"],
                row["bundle_id"],
                cast(Literal["development", "production"], row["environment"]),
                row["registration_version"],
                row["updated_at"],
            ),
            NotificationIntent(row["title"], row["body"], row["route"], row["tag"]),
            row["attempts"],
            row["created_at"],
        )
        for row in rows
    )


def record_result(
    conn: sqlite3.Connection,
    delivery: PendingAPNsDelivery,
    result: APNsResult,
    now: int,
) -> None:
    device = delivery.device
    if result.dead_token:
        # A response to an older send must not revoke a later registration. Apple
        # timestamps are milliseconds; the version also covers same-second refreshes.
        conn.execute(
            "DELETE FROM notification_apns_devices WHERE device_id=? "
            "AND registration_version=? AND (? IS NULL OR updated_at*1000 <= ?)",
            (
                device.device_id,
                device.registration_version,
                result.invalidated_at_ms,
                result.invalidated_at_ms,
            ),
        )
    attempts = delivery.attempts + 1
    next_attempt = now + APNS_RETRY_BASE_SECONDS * 2 ** (attempts - 1)
    status = "failed"
    if result.delivered:
        status = "delivered"
    elif (
        result.retryable
        and attempts < APNS_MAX_ATTEMPTS
        and next_attempt < delivery.created_at + APNS_DELIVERY_TTL_SECONDS
    ):
        status = "retry"
    conn.execute(
        "UPDATE notification_apns_deliveries SET status=?,attempts=?,next_attempt_at=?,"
        "delivered_at=?,last_error=? WHERE subject_kind=? AND subject_id=? "
        "AND notification_type=? AND generation=? AND device_id=?",
        (
            status,
            attempts,
            next_attempt,
            now if result.delivered else None,
            result.error,
            delivery.edge.subject_kind,
            delivery.edge.subject_id,
            delivery.edge.notification_type,
            delivery.edge.generation,
            device.device_id,
        ),
    )
