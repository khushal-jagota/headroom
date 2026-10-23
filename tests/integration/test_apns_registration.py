from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

OWNER_HEADERS = {"Tailscale-User-Login": "owner@example.com"}
DEVICE = {"device_token": "0011aaff", "bundle_id": "app.panels.ios", "environment": "development"}
ENDPOINT = "/api/notifications/apns/devices"


def test_authenticated_registration_is_durable_validated_and_revocable(tmp_path: Path) -> None:
    database = str(tmp_path / "registration.db")
    with closing(connect(database)) as conn:
        create_schema(conn)
    config = load_config(
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": database,
            "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
            "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "owner@example.com",
            "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "https://panels.example",
            "PLAN_APNS_KEY_PATH": str(tmp_path / "not-needed-for-registration.p8"),
            "PLAN_APNS_KEY_ID": "KEY1234567",
            "PLAN_APNS_TEAM_ID": "TEAM123456",
            "PLAN_APNS_BUNDLE_IDS": "app.panels.ios",
        }
    )
    app = create_app(config, build_clock(config), lambda: connect(database))
    # In-process HTTP reaches the actual routers and ingress middleware. No runtime
    # providers, server thread, Apple connection or browser host is needed.
    client = TestClient(app)
    for headers, expected in (
        ({}, 400),
        ({"Tailscale-User-Login": "stranger@example.com"}, 403),
        ({"X-Plan-Actor": "chief"}, 400),
    ):
        assert client.put(ENDPOINT, json=DEVICE, headers=headers).status_code == expected
        assert (
            client.request("DELETE", ENDPOINT, json=DEVICE, headers=headers).status_code == expected
        )
    assert (
        client.put(
            ENDPOINT, json=DEVICE, headers={**OWNER_HEADERS, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    for changes in (
        {"device_token": "NOT-HEX"},
        {"device_token": "abc"},
        {"environment": "sandbox"},
        {"bundle_id": "other.app"},
    ):
        assert client.put(
            ENDPOINT, json={**DEVICE, **changes}, headers=OWNER_HEADERS
        ).status_code in {400, 422}
    for _ in range(2):
        assert client.put(ENDPOINT, json=DEVICE, headers=OWNER_HEADERS).json() == {
            "registered": True
        }
    production = {**DEVICE, "environment": "production"}
    assert client.put(ENDPOINT, json=production, headers=OWNER_HEADERS).status_code == 200
    with closing(connect(database)) as conn:
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT owner_id,environment,registration_version "
                "FROM notification_apns_devices ORDER BY environment"
            )
        ] == [("owner", "development", 2), ("owner", "production", 1)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # Key removal must stop new registrations without preventing existing revocation.
    app.state.config = replace(config, apns=None)
    assert client.put(ENDPOINT, json=DEVICE, headers=OWNER_HEADERS).status_code == 503
    assert client.request("DELETE", ENDPOINT, json=DEVICE, headers=OWNER_HEADERS).json() == {
        "removed": True
    }
    assert client.request("DELETE", ENDPOINT, json=DEVICE, headers=OWNER_HEADERS).json() == {
        "removed": False
    }
    with closing(connect(database)) as conn:
        assert [
            row[0] for row in conn.execute("SELECT environment FROM notification_apns_devices")
        ] == ["production"]
    client.close()
