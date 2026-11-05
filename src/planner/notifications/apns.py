"""The sole boundary to Apple's HTTP/2 protocol, JWTs and response reasons."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
import jwt

from planner.notifications.config import (
    APNS_DELIVERY_TTL_SECONDS,
    APNS_JWT_LIFETIME_SECONDS,
    APNS_PAYLOAD_MAX_BYTES,
    APNS_TIMEOUT_SECONDS,
)
from planner.notifications.contracts import APNsConfig, APNsResult, PendingAPNsDelivery


class APNsAdapter(Protocol):
    def send(
        self,
        delivery: PendingAPNsDelivery,
        *,
        server_url: str,
        now: int,
    ) -> APNsResult: ...

    def close(self) -> None: ...


class _RedactDeviceToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX's INFO request log otherwise includes the device token in its URL.
        record.msg = re.sub(r"(/3/device/)[0-9a-f]+", r"\1[redacted]", record.getMessage())
        record.msg = re.sub(r"bearer [A-Za-z0-9_.-]+", "bearer [redacted]", record.msg)
        record.args = ()
        return True


def canonical_server_origin(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            return None
        _ = parts.port
        return f"https://{parts.netloc}"
    except ValueError:
        return None


class TokenAPNsAdapter:
    def __init__(
        self,
        config: APNsConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._jwt: str | None = None
        self._jwt_issued_at = 0
        self._client = httpx.Client(
            http2=True,
            http1=False,
            timeout=APNS_TIMEOUT_SECONDS,
            transport=transport,
            trust_env=False,
        )
        for name in (
            "httpx",
            "httpcore.http2",
            "httpcore.connection",
            "httpcore.proxy",
            "hpack.hpack",
        ):
            logger = logging.getLogger(name)
            if not any(isinstance(item, _RedactDeviceToken) for item in logger.filters):
                logger.addFilter(_RedactDeviceToken())

    def close(self) -> None:
        self._client.close()

    def _provider_token(self, now: int) -> str:
        if self._jwt is None or not 0 <= now - self._jwt_issued_at < APNS_JWT_LIFETIME_SECONDS:
            self._jwt = jwt.encode(
                {"iss": self._config.team_id, "iat": now},
                Path(self._config.key_path).read_bytes(),
                algorithm="ES256",
                headers={"kid": self._config.key_id},
            )
            self._jwt_issued_at = now
        return self._jwt

    def send(
        self,
        delivery: PendingAPNsDelivery,
        *,
        server_url: str,
        now: int,
    ) -> APNsResult:
        device = delivery.device
        origin = canonical_server_origin(server_url)
        if origin is None or device.bundle_id not in self._config.bundle_ids:
            return APNsResult(False, error="Native delivery configuration does not match")
        intent = delivery.intent
        payload = json.dumps(
            {
                "aps": {
                    "alert": {"title": intent.title, "body": intent.body},
                    "sound": "default",
                    "thread-id": intent.tag,
                },
                "server_url": origin,
                "route": intent.route,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > APNS_PAYLOAD_MAX_BYTES:
            return APNsResult(False, error="Native notification payload exceeds the size limit")
        host = (
            "api.sandbox.push.apple.com"
            if device.environment == "development"
            else "api.push.apple.com"
        )
        try:
            response = self._client.post(
                f"https://{host}/3/device/{device.device_token}",
                content=payload,
                headers={
                    "authorization": f"bearer {self._provider_token(now)}",
                    "content-type": "application/json",
                    "apns-topic": device.bundle_id,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-expiration": str(delivery.created_at + APNS_DELIVERY_TTL_SECONDS),
                    "apns-collapse-id": hashlib.sha256(intent.tag.encode()).hexdigest(),
                },
            )
        except Exception:
            # Exceptions may contain the request URL, token, or signing key details.
            return APNsResult(
                False, retryable=True, error="Native push transport or signing failed"
            )
        if response.status_code == 200:
            return APNsResult(True)
        try:
            detail = response.json()
        except ValueError:
            detail = {}
        reason = detail.get("reason") if isinstance(detail, dict) else None
        if not isinstance(reason, str):
            reason = None
        timestamp = detail.get("timestamp") if isinstance(detail, dict) else None
        dead = (response.status_code == 410 and reason in {"Unregistered", "ExpiredToken"}) or (
            response.status_code == 400 and reason in {"BadDeviceToken", "DeviceTokenNotForTopic"}
        )
        return APNsResult(
            False,
            dead_token=dead,
            retryable=response.status_code in {429, 500, 503},
            error=f"Native push rejected delivery (HTTP {response.status_code})",
            invalidated_at_ms=(
                timestamp if response.status_code == 410 and type(timestamp) is int else None
            ),
        )
