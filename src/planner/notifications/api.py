"""Authenticated notification settings and device-subscription HTTP surface."""

from __future__ import annotations

import sqlite3
from typing import Literal, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from planner.core.authctx import require_authenticated_owner
from planner.core.errors import ErrorCode, PlannerError
from planner.notifications import apns_data, data
from planner.notifications.apns import canonical_server_origin
from planner.notifications.contracts import (
    NOTIFICATION_SUBJECTS,
    NOTIFICATION_TYPE_BY_ID,
    notification_preference_is_valid,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


class PreferenceWrite(BaseModel):
    enabled: bool


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=8, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class SubscriptionWrite(BaseModel):
    endpoint: str = Field(min_length=12, max_length=4096)
    keys: SubscriptionKeys


class APNsDeviceWrite(BaseModel):
    # APNs tokens are variable-length bytes, encoded as lowercase hex by iOS.
    device_token: str = Field(min_length=2, max_length=512, pattern=r"^(?:[0-9a-f]{2})+$")
    bundle_id: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.-]+$")
    environment: Literal["development", "production"]


@router.put("/apns/devices")
def register_apns_device(body: APNsDeviceWrite, request: Request) -> dict[str, bool]:
    require_authenticated_owner(request)
    config = request.app.state.config.apns
    if (
        config is None
        or canonical_server_origin(request.app.state.config.trusted_ingress_canonical_origin)
        is None
    ):
        raise PlannerError(ErrorCode.gateway_offline, "native push delivery is not configured")
    if body.bundle_id not in config.bundle_ids:
        raise PlannerError(ErrorCode.validation, "native push bundle ID is not allowed")
    conn = _conn(request)
    try:
        apns_data.register_device(
            conn,
            device_token=body.device_token,
            bundle_id=body.bundle_id,
            environment=body.environment,
            now=request.app.state.clock.now_unix(),
        )
        return {"registered": True}
    finally:
        conn.close()


@router.delete("/apns/devices")
def unregister_apns_device(body: APNsDeviceWrite, request: Request) -> dict[str, bool]:
    require_authenticated_owner(request)
    # Revocation remains available after keys or an allowed bundle have been removed.
    conn = _conn(request)
    try:
        return {
            "removed": apns_data.remove_device(
                conn,
                device_token=body.device_token,
                bundle_id=body.bundle_id,
                environment=body.environment,
            )
        }
    finally:
        conn.close()


def _conn(request: Request) -> sqlite3.Connection:
    return cast(sqlite3.Connection, request.app.state.conn_factory())


def _settings_payload(conn: sqlite3.Connection) -> dict[str, object]:
    preferences = data.resolved_preferences(conn)
    identity = data.read_web_push_identity(conn)
    return {
        "subjects": [
            {
                "key": subject.key,
                "label": subject.label,
                "types": [
                    {
                        "id": notification_type,
                        "label": NOTIFICATION_TYPE_BY_ID[notification_type].label,
                        "description": NOTIFICATION_TYPE_BY_ID[notification_type].description,
                        "enabled": preferences[(subject.key, notification_type)],
                    }
                    for notification_type in subject.notification_type_ids
                ],
            }
            for subject in NOTIFICATION_SUBJECTS
        ],
        "vapid_public_key": identity.public_key,
    }


@router.get("/settings")
def read_settings(request: Request) -> dict[str, object]:
    conn = _conn(request)
    try:
        return _settings_payload(conn)
    finally:
        conn.close()


@router.put("/preferences/{subject_key}/{notification_type}")
def write_preference(
    subject_key: str,
    notification_type: str,
    body: PreferenceWrite,
    request: Request,
) -> dict[str, object]:
    if not notification_preference_is_valid(subject_key, notification_type):
        raise PlannerError(
            ErrorCode.not_found,
            f"notification preference not found: {subject_key}/{notification_type}",
        )
    conn = _conn(request)
    try:
        data.set_preference(
            conn,
            subject_key,
            notification_type,
            body.enabled,
            request.app.state.clock.now_unix(),
        )
        return _settings_payload(conn)
    finally:
        conn.close()


@router.post("/subscriptions")
def register_subscription(body: SubscriptionWrite, request: Request) -> dict[str, object]:
    endpoint = urlsplit(body.endpoint)
    if endpoint.scheme != "https" or not endpoint.netloc or endpoint.username is not None:
        raise PlannerError(ErrorCode.validation, "push subscription endpoint must be an HTTPS URL")
    conn = _conn(request)
    try:
        subscription = data.register_subscription(
            conn,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            now=request.app.state.clock.now_unix(),
        )
        return {"subscription_id": subscription.subscription_id}
    finally:
        conn.close()


@router.delete("/subscriptions/{subscription_id}")
def delete_subscription(subscription_id: str, request: Request) -> dict[str, bool]:
    conn = _conn(request)
    try:
        return {"removed": data.remove_subscription(conn, subscription_id)}
    finally:
        conn.close()
