from __future__ import annotations

import json
import subprocess
from pathlib import Path
from sqlite3 import Connection

from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.clock import TestClock as MutableClock
from planner.core.clock import parse_fake_now
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.notifications import data as notifications_data
from planner.notifications.contracts import (
    NotificationIntent,
    PushSubscription,
    WebPushIdentity,
    WebPushResult,
)
from planner.notifications.runtime import NotificationLoop
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, Ticket


def _ticket(conn: Connection, now: int) -> Ticket:
    return tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Phone-worthy work",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
    )


def _subscribe(conn: Connection, now: int = 1, name: str = "subscription") -> str:
    """A registered device, because a delivery row is the only thing a notification is."""
    return notifications_data.register_subscription(
        conn,
        endpoint=f"https://push.example/{name}",
        p256dh="p256dh-value",
        auth="auth-value",
        now=now,
    ).subscription_id


def _service_worker_push_results(payloads: list[object]) -> list[dict[str, object]]:
    repository_root = Path(__file__).parents[2]
    result = subprocess.run(
        [
            "node",
            str(repository_root / "tests/support/service_worker_push_harness.mjs"),
            str(repository_root / "static/service-worker.js"),
        ],
        input=json.dumps(payloads),
        text=True,
        capture_output=True,
        check=True,
    )
    decoded: object = json.loads(result.stdout)
    assert isinstance(decoded, list)
    normalized: list[dict[str, object]] = []
    for item in decoded:
        assert isinstance(item, dict)
        normalized.append({str(key): value for key, value in item.items()})
    return normalized


def test_notification_settings_api_serves_catalogue_and_persists_choice(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "api.db"
    with connect(str(db_path)) as bootstrap:
        create_schema(bootstrap)
    cfg = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "lock"),
        },
    )
    clock = MutableClock(parse_fake_now("2026-07-29T12:00:00+02:00"))
    app = create_app(cfg, clock, lambda: connect(str(db_path)))
    with TestClient(app) as client:
        settings = client.get("/api/notifications/settings")
        assert settings.status_code == 200
        payload = settings.json()
        assert [subject["key"] for subject in payload["subjects"]] == [
            "tickets",
            "chief_of_staff",
            "sprint_item_supervisors",
        ]
        types_by_subject = {
            subject["key"]: {item["id"] for item in subject["types"]}
            for subject in payload["subjects"]
        }
        assert sum(len(subject["types"]) for subject in payload["subjects"]) == 8
        assert types_by_subject["tickets"] == {
            "awaiting_reply",
            "awaiting_approval",
            "assigned",
            "errored",
        }
        assert types_by_subject["chief_of_staff"] == {
            "awaiting_reply",
            "errored",
        }
        assert types_by_subject["sprint_item_supervisors"] == {
            "awaiting_reply",
            "errored",
        }
        enabled_by_subject = {
            subject["key"]: {item["id"]: item["enabled"] for item in subject["types"]}
            for subject in payload["subjects"]
        }
        assert enabled_by_subject["sprint_item_supervisors"] == {
            "awaiting_reply": True,
            "errored": False,
        }
        assert all(enabled_by_subject["tickets"].values())
        assert all(enabled_by_subject["chief_of_staff"].values())
        assert payload["vapid_public_key"]

        changed = client.put(
            "/api/notifications/preferences/chief_of_staff/errored",
            json={"enabled": False},
        )
        assert changed.status_code == 200
        resolved = {
            subject["key"]: {item["id"]: item["enabled"] for item in subject["types"]}
            for subject in changed.json()["subjects"]
        }
        assert resolved["chief_of_staff"]["errored"] is False
        assert resolved["tickets"]["errored"] is True

        invalid = client.put(
            "/api/notifications/preferences/chief_of_staff/awaiting_approval",
            json={"enabled": False},
        )
        assert invalid.status_code == 404


def test_the_loop_sends_one_edge_to_the_registered_device_and_records_it(
    tmp_path: Path,
) -> None:
    """The whole loop, against a device that is not anyone's phone."""
    db_path = tmp_path / "loop.db"
    conn = connect(str(db_path))
    create_schema(conn)
    notifications_data.get_or_create_web_push_identity(conn, 1)
    subscription_id = _subscribe(conn, name="loop-device")
    ticket = _ticket(conn, 1)
    conn.close()

    sent: list[tuple[str, NotificationIntent]] = []

    class RecordingAdapter:
        def send(
            self,
            subscription: PushSubscription,
            intent: NotificationIntent,
            identity: WebPushIdentity,
            *,
            subject: str,
        ) -> WebPushResult:
            sent.append((subscription.subscription_id, intent))
            return WebPushResult(delivered=True)

    loop = NotificationLoop(
        str(db_path),
        MutableClock(parse_fake_now("2026-09-20T05:00:00+01:00")),
        canonical_origin="https://panels.example",
        adapter=RecordingAdapter(),
    )
    assert loop.poll_once() == 2
    assert [subscription for subscription, _ in sent] == [subscription_id] * 2
    assert {intent.route for _, intent in sent} == {f"/#/workspace/{ticket.id}"}
    assert {intent.tag for _, intent in sent} == {f"panels-ticket-{ticket.id}"}

    # Nothing is sent twice, and the log says what went out.
    assert loop.poll_once() == 0
    with connect(str(db_path)) as conn:
        assert [
            (str(row["notification_type"]), str(row["status"]), int(row["attempts"]))
            for row in conn.execute(
                "SELECT notification_type, status, attempts FROM notification_deliveries "
                "ORDER BY notification_type"
            )
        ] == [("assigned", "delivered", 1), ("awaiting_approval", "delivered", 1)]
