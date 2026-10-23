from __future__ import annotations

import json
import logging
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from tests.unit.test_notifications import _subscribe, _ticket

from planner.core.clock import TestClock as MutableClock
from planner.core.clock import parse_fake_now
from planner.core.config import load_config
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.notifications import apns_data, data
from planner.notifications.apns import TokenAPNsAdapter
from planner.notifications.contracts import (
    NOTIFICATION_TYPES,
    APNsConfig,
    APNsDevice,
    AttentionEdge,
    EdgeKey,
    NotificationIntent,
    PendingAPNsDelivery,
)
from planner.notifications.logic.policy import decide_notification
from planner.notifications.runtime import NotificationLoop

NOW = 1_790_000_000
ORIGIN = "https://panels.example"
TOKEN = "aabb" * 16


@pytest.fixture
def credentials(tmp_path: Path) -> APNsConfig:
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "test.p8"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return APNsConfig(str(path), "KEY1234567", "TEAM123456", ("app.panels.ios",))


def _delivery(
    environment: Literal["development", "production"] = "development",
) -> PendingAPNsDelivery:
    return PendingAPNsDelivery(
        EdgeKey("ticket", "t_42", "awaiting_approval", 1),
        APNsDevice("device", TOKEN, "app.panels.ios", environment, 1, NOW),
        NotificationIntent(
            "Work · Needs approval",
            "A proposal is waiting.",
            "/#/workspace/t_42",
            "panels-ticket-t_42",
        ),
        0,
        NOW,
    )


def test_apns_wire_payload_signing_environments_and_log_privacy(
    credentials: APNsConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    adapter = TokenAPNsAdapter(credentials, transport=httpx.MockTransport(send))
    caplog.set_level(logging.INFO, logger="httpx")
    try:
        for environment in ("development", "production"):
            delivery = _delivery(environment)
            assert adapter.send(delivery, server_url=ORIGIN + "/", now=NOW).delivered
        assert [request.url.host for request in requests] == [
            "api.sandbox.push.apple.com",
            "api.push.apple.com",
        ]
        request = requests[0]
        assert request.method == "POST"
        assert request.url.path == f"/3/device/{TOKEN}"
        assert request.headers["apns-topic"] == "app.panels.ios"
        assert request.headers["apns-push-type"] == "alert"
        assert request.headers["apns-priority"] == "10"
        assert request.headers["apns-expiration"] == str(NOW + 300)
        assert len(request.headers["apns-collapse-id"].encode()) <= 64
        assert json.loads(request.content) == {
            "aps": {
                "alert": {"title": "Work · Needs approval", "body": "A proposal is waiting."},
                "sound": "default",
                "thread-id": "panels-ticket-t_42",
            },
            "server_url": ORIGIN,
            "route": "/#/workspace/t_42",
        }
        assert len(request.content) < 4096
        provider_token = request.headers["authorization"].removeprefix("bearer ")
        key = serialization.load_pem_private_key(Path(credentials.key_path).read_bytes(), None)
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        assert jwt.decode(
            provider_token, key.public_key(), algorithms=["ES256"], options={"verify_iat": False}
        ) == {"iss": "TEAM123456", "iat": NOW}
        assert jwt.get_unverified_header(provider_token)["kid"] == "KEY1234567"
        assert requests[1].headers["authorization"] == request.headers["authorization"]
        assert TOKEN not in caplog.text
        assert provider_token not in caplog.text
        assert "[redacted]" in caplog.text
        assert adapter.send(_delivery(), server_url=ORIGIN, now=NOW + 3000).delivered
        assert requests[-1].headers["authorization"] != request.headers["authorization"]
    finally:
        adapter.close()


def test_unsafe_origin_and_oversize_payload_never_reach_apple(credentials: APNsConfig) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid native payload reached the provider")

    adapter = TokenAPNsAdapter(credentials, transport=httpx.MockTransport(unexpected))
    try:
        for origin in (
            "http://panels.example",
            "https://user:pass@panels.example",
            ORIGIN + "/path",
        ):
            assert not adapter.send(_delivery(), server_url=origin, now=NOW).delivered
        large = replace(
            _delivery(), intent=NotificationIntent("x" * 4096, "body", "/#/review", "tag")
        )
        result = adapter.send(large, server_url=ORIGIN, now=NOW)
        assert not result.delivered and not result.retryable
    finally:
        adapter.close()


@pytest.mark.parametrize(
    ("status", "reason", "dead", "retryable"),
    [
        (410, "Unregistered", True, False),
        (400, "BadDeviceToken", True, False),
        (400, "DeviceTokenNotForTopic", True, False),
        (403, "InvalidProviderToken", False, False),
        (429, "TooManyRequests", False, True),
        (503, "ServiceUnavailable", False, True),
        (400, ["malformed"], False, False),
    ],
)
def test_provider_rejection_is_classified_without_echoing_response(
    credentials: APNsConfig,
    status: int,
    reason: object,
    dead: bool,
    retryable: bool,
) -> None:
    adapter = TokenAPNsAdapter(
        credentials,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"reason": reason, "timestamp": NOW * 1000})
        ),
    )
    try:
        result = adapter.send(_delivery(), server_url=ORIGIN, now=NOW)
        assert not result.delivered
        assert result.dead_token is dead
        assert result.retryable is retryable
        assert result.error == f"Native push rejected delivery (HTTP {status})"
    finally:
        adapter.close()


def test_native_queue_is_independent_durable_and_removes_dead_tokens(
    tmp_path: Path,
    credentials: APNsConfig,
) -> None:
    database = str(tmp_path / "queue.db")
    with closing(connect(database)) as conn:
        create_schema(conn)
        data.get_or_create_web_push_identity(conn, NOW)
        _subscribe(conn)
        for token in (TOKEN, "ccdd" * 16):
            apns_data.register_device(
                conn,
                device_token=token,
                bundle_id="app.panels.ios",
                environment="development",
                now=NOW,
            )
        ticket = _ticket(conn, NOW)
        assert data.queue_deliveries(conn, NOW) == 1
        assert len(apns_data.pending_deliveries(conn, NOW)) == 2
        # Browser success cannot consume either native record.
        (web_delivery,) = data.pending_deliveries(conn, NOW)
        data.record_delivery_result(
            conn, web_delivery, now=NOW, delivered=True, expired=False, error=None
        )

    requests: list[str] = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith(TOKEN):
            return httpx.Response(410, json={"reason": "Unregistered", "timestamp": NOW * 1000})
        return httpx.Response(200)

    adapter = TokenAPNsAdapter(credentials, transport=httpx.MockTransport(send))
    clock = MutableClock(parse_fake_now("2026-09-20T05:00:00+01:00"))
    # The queue's fixed time is what this test controls, independent of wall time.
    clock.set(datetime.fromtimestamp(NOW, UTC))
    loop = NotificationLoop(database, clock, canonical_origin=ORIGIN, apns_adapter=adapter)
    try:
        assert loop.poll_once() == 2
        assert loop.poll_once() == 0
        assert len(requests) == 2
        with closing(connect(database)) as conn:
            assert (
                conn.execute("SELECT device_token FROM notification_apns_devices").fetchone()[0]
                == "ccdd" * 16
            )
            assert tuple(
                conn.execute("SELECT status,attempts FROM notification_apns_deliveries").fetchone()
            ) == ("delivered", 1)
            assert (
                conn.execute(
                    "SELECT status FROM notification_deliveries WHERE subject_id=?", (ticket.id,)
                ).fetchone()[0]
                == "delivered"
            )
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            data.set_preference(conn, "tickets", "awaiting_approval", False, NOW + 1)
            _ticket(conn, NOW + 1)
            assert data.queue_deliveries(conn, NOW + 1) == 1
            assert (
                conn.execute("SELECT count(*) FROM notification_apns_deliveries").fetchone()[0] == 1
            )
    finally:
        loop.stop()


def test_native_retries_expire_and_late_dead_response_preserves_new_registration(
    tmp_path: Path,
    credentials: APNsConfig,
) -> None:
    database = str(tmp_path / "retry.db")
    with closing(connect(database)) as conn:
        create_schema(conn)
        data.get_or_create_web_push_identity(conn, NOW)
        apns_data.register_device(
            conn, device_token=TOKEN, bundle_id="app.panels.ios", environment="development", now=NOW
        )
        _ticket(conn, NOW)
        data.queue_deliveries(conn, NOW)
        (delivery,) = apns_data.pending_deliveries(conn, NOW)
        apns_data.register_device(
            conn, device_token=TOKEN, bundle_id="app.panels.ios", environment="development", now=NOW
        )
        adapter = TokenAPNsAdapter(
            credentials,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    410, json={"reason": "Unregistered", "timestamp": NOW * 1000}
                )
            ),
        )
        try:
            apns_data.record_result(
                conn, delivery, adapter.send(delivery, server_url=ORIGIN, now=NOW), NOW
            )
        finally:
            adapter.close()
        assert (
            conn.execute("SELECT registration_version FROM notification_apns_devices").fetchone()[0]
            == 2
        )
        # A new attention edge, on the same device, exercises bounded provider retries.
        conn.execute("UPDATE notification_attention_edges SET decided=0")
        conn.execute("DELETE FROM notification_apns_deliveries")
        data.queue_deliveries(conn, NOW)
        adapter = TokenAPNsAdapter(
            credentials, transport=httpx.MockTransport(lambda request: httpx.Response(503))
        )
        try:
            for due in (NOW, NOW + 30, NOW + 90, NOW + 210):
                (delivery,) = apns_data.pending_deliveries(conn, due)
                result = adapter.send(delivery, server_url=ORIGIN, now=due)
                apns_data.record_result(conn, delivery, result, due)
            assert apns_data.pending_deliveries(conn, NOW + 240) == ()
            assert tuple(
                conn.execute("SELECT status,attempts FROM notification_apns_deliveries").fetchone()
            ) == ("failed", 4)
            conn.execute(
                "UPDATE notification_apns_deliveries SET status='retry',next_attempt_at=?",
                (NOW + 300,),
            )
            assert apns_data.pending_deliveries(conn, NOW + 300) == ()
            assert (
                conn.execute("SELECT status FROM notification_apns_deliveries").fetchone()[0]
                == "expired"
            )
        finally:
            adapter.close()


def test_missing_credentials_skip_network_and_do_not_claim_delivery(tmp_path: Path) -> None:
    config = load_config(env={"PLAN_APNS_KEY_ID": "partial"})
    assert config.apns is None
    database = str(tmp_path / "unconfigured.db")
    with closing(connect(database)) as conn:
        create_schema(conn)
        data.get_or_create_web_push_identity(conn, NOW)
        apns_data.register_device(
            conn, device_token=TOKEN, bundle_id="app.panels.ios", environment="production", now=NOW
        )
        _ticket(conn, NOW)
    clock = MutableClock(parse_fake_now("2026-09-20T05:00:00+01:00"))
    clock.set(datetime.fromtimestamp(NOW, UTC))
    loop = NotificationLoop(database, clock, canonical_origin=ORIGIN, apns_config=config.apns)
    try:
        assert loop.poll_once() == 0
        with closing(connect(database)) as conn:
            assert tuple(
                conn.execute("SELECT status,attempts FROM notification_apns_deliveries").fetchone()
            ) == ("pending", 0)
    finally:
        loop.stop()


def test_policy_preserves_native_destinations_and_bounds_lock_screen_copy() -> None:
    for subject, route in (
        (Principal(PrincipalKind.ticket, "t_42"), "/#/workspace/t_42"),
        (Principal(PrincipalKind.manager, "m_9"), "/#/workspace/item/m_9"),
        (CHIEF_PRINCIPAL, "/#/agents/chief-of-staff"),
    ):
        for notification_type in NOTIFICATION_TYPES:
            edge = AttentionEdge(
                EdgeKey(subject.kind.value, subject.id, notification_type.id, 1),
                subject,
                "A long work label " * 30,
                NOW,
            )
            intent = decide_notification(edge, enabled=True)
            assert intent is not None and intent.route == route
            assert intent.title.endswith(" · " + notification_type.label)
            assert len(intent.title.split(" · ")[0]) <= 61
            assert "A long work label " * 30 not in intent.body
            assert decide_notification(edge, enabled=False) is None
