"""The sole boundary to Web Push protocol and provider response shapes."""

from __future__ import annotations

import json
from typing import Protocol

from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]

from planner.notifications.contracts import (
    NotificationIntent,
    PushSubscription,
    WebPushIdentity,
    WebPushResult,
)


class WebPushAdapter(Protocol):
    def send(
        self,
        subscription: PushSubscription,
        intent: NotificationIntent,
        identity: WebPushIdentity,
        *,
        subject: str,
    ) -> WebPushResult: ...


class StandardsWebPushAdapter:
    def send(
        self,
        subscription: PushSubscription,
        intent: NotificationIntent,
        identity: WebPushIdentity,
        *,
        subject: str,
    ) -> WebPushResult:
        payload = json.dumps(
            {
                "title": intent.title,
                "body": intent.body,
                "route": intent.route,
                "tag": intent.tag,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=payload,
                vapid_private_key=identity.private_key,
                vapid_claims={"sub": subject},
                ttl=300,
                timeout=10,
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            return WebPushResult(
                delivered=False,
                expired=status in {404, 410},
                error=f"Web Push rejected delivery ({status or 'transport error'})",
            )
        except Exception as exc:
            return WebPushResult(delivered=False, error=f"Web Push failed: {type(exc).__name__}")
        return WebPushResult(delivered=True)
