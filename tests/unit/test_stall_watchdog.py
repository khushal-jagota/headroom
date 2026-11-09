"""The stall watchdog unit: what it alarms on, how often, and through which channels.

Risk: the watchdog writes to the live database, misses a stopped checker, alarms on
every 30 s run (or never again after a stall returns), ignores the owner's switch, or
lets a stuck server's Chief call stop the push. Nothing covered this before. Layer:
run_watchdog over a fresh schema in tmp_path with fake Web Push and APNs adapters and a
fake Chief call; message_chief against an httpx MockTransport. No real notification or
Chief message is sent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from planner.core.db import connect, create_schema
from planner.notifications import apns_data
from planner.notifications import data as notifications_data
from planner.notifications.contracts import (
    APNsResult,
    NotificationIntent,
    PendingAPNsDelivery,
    PushSubscription,
    WebPushIdentity,
    WebPushResult,
)
from planner.stalls import alarms
from planner.stalls.configuration import CHECKER_HEARTBEAT_STALE_SECONDS
from planner.stalls.watchdog import WATCHDOG_STATE_FILE_NAME, WatchdogRun, run_watchdog

S = 1000
NOW_MS = 1_800_000_000_000  # 2027-01-15 08:00 UTC
ORIGIN = "https://panels.example"


class _WebPush:
    def __init__(self) -> None:
        self.sent: list[NotificationIntent] = []

    def send(
        self,
        subscription: PushSubscription,
        intent: NotificationIntent,
        identity: WebPushIdentity,
        *,
        subject: str,
    ) -> WebPushResult:
        self.sent.append(intent)
        return WebPushResult(delivered=True)


class _APNs:
    def __init__(self) -> None:
        self.sent: list[PendingAPNsDelivery] = []

    def send(self, delivery: PendingAPNsDelivery, *, server_url: str, now: int) -> APNsResult:
        self.sent.append(delivery)
        return APNsResult(True)

    def close(self) -> None:
        pass


class _Chief:
    def __init__(self, answer: Callable[[str], bool]) -> None:
        self.messages: list[str] = []
        self._answer = answer

    def __call__(self, text: str) -> bool:
        self.messages.append(text)
        return self._answer(text)


def _database(tmp_path: Path, *, heartbeat_at_ms: int | None = NOW_MS) -> str:
    path = str(tmp_path / "planner.db")
    conn = connect(path)
    try:
        create_schema(conn)
        notifications_data.register_subscription(
            conn, endpoint="https://push.example/1", p256dh="key", auth="auth", now=1
        )
        apns_data.register_device(
            conn,
            device_token="ab" * 32,
            bundle_id="app.panels.ios",
            environment="production",
            now=1,
        )
        if heartbeat_at_ms is not None:
            conn.execute(
                "INSERT INTO stall_checker_heartbeat(singleton,checked_at_ms) VALUES (1,?)",
                (heartbeat_at_ms,),
            )
    finally:
        conn.close()
    return path


def _open_finding(db_path: str, *, stalled_since_ms: int) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO stall_findings(finding_key,kind,conversation_id,ticket_id,ticket_title,"
            "detail,stalled_since_ms,last_seen_at_ms,cleared_at_ms) VALUES "
            "('mac_items_not_moving:c1:7','mac_items_not_moving','c1','t_login',"
            "'Fix the login bug','The Mac has not delivered item 7',?,?,NULL) "
            "ON CONFLICT(finding_key) DO UPDATE SET stalled_since_ms=excluded.stalled_since_ms,"
            "cleared_at_ms=NULL",
            (stalled_since_ms, NOW_MS),
        )
    finally:
        conn.close()


def _clear_findings(db_path: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute("UPDATE stall_findings SET cleared_at_ms=?", (NOW_MS,))
    finally:
        conn.close()


def _database_bytes(db_path: str) -> str:
    digest = hashlib.sha256()
    for suffix in ("", "-wal"):
        path = Path(db_path + suffix)
        digest.update(path.read_bytes() if path.exists() else b"")
    return digest.hexdigest()


class _Watchdog:
    def __init__(self, db_path: str, state_dir: Path, chief: _Chief) -> None:
        self.db_path = db_path
        self.state_dir = state_dir
        self.web_push = _WebPush()
        self.apns = _APNs()
        self.chief = chief

    def run(self, now_ms: int = NOW_MS) -> WatchdogRun:
        before = _database_bytes(self.db_path)
        result = run_watchdog(
            self.db_path,
            self.state_dir,
            now_ms=now_ms,
            web_push=self.web_push,
            apns=self.apns,
            push_origin=ORIGIN,
            message_chief=self.chief,
        )
        assert _database_bytes(self.db_path) == before, "the watchdog wrote to the database"
        return result

    def state(self) -> list[str]:
        stored = json.loads((self.state_dir / WATCHDOG_STATE_FILE_NAME).read_text())
        return list(stored["alarmed"])


def test_each_stall_episode_alarms_once_through_push_and_the_chief(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    watchdog = _Watchdog(db_path, tmp_path / "state", _Chief(lambda text: True))
    stalled_since = NOW_MS - 200 * S  # 07:56:40 UTC
    _open_finding(db_path, stalled_since_ms=stalled_since)

    first = watchdog.run()
    assert len(first.raised) == 1 and first.pushes_delivered == 2 and first.chief_accepted
    (text,) = watchdog.chief.messages
    assert text == (
        'Ticket "Fix the login bug" (conversation c1): Mac events are not reaching the VPS. '
        "The Mac has not delivered item 7, since 07:56 UTC."
    )
    (pushed,) = watchdog.web_push.sent
    assert pushed.body == text and pushed.route == "/#/workspace/t_login"
    assert [delivery.intent for delivery in watchdog.apns.sent] == [pushed]

    # The next runs see the same open finding and stay quiet.
    assert watchdog.run().raised == ()
    assert watchdog.run(NOW_MS + 30 * S).raised == ()
    assert len(watchdog.chief.messages) == len(watchdog.web_push.sent) == 1

    # It clears, and the record lets go of it; when it comes back, it alarms again.
    _clear_findings(db_path)
    assert watchdog.run().due == ()
    assert watchdog.state() == []
    _open_finding(db_path, stalled_since_ms=NOW_MS - 10 * S)
    assert len(watchdog.run().raised) == 1
    assert len(watchdog.chief.messages) == len(watchdog.web_push.sent) == 2


@pytest.mark.parametrize(
    ("heartbeat_at_ms", "said"),
    [
        (NOW_MS - (CHECKER_HEARTBEAT_STALE_SECONDS + 1) * S, "has not run since 07:58 UTC"),
        (None, "has never run"),
    ],
)
def test_a_stale_or_missing_checker_heartbeat_alarms(
    tmp_path: Path, heartbeat_at_ms: int | None, said: str
) -> None:
    db_path = _database(tmp_path, heartbeat_at_ms=heartbeat_at_ms)
    watchdog = _Watchdog(db_path, tmp_path / "state", _Chief(lambda text: True))
    (alarm,) = watchdog.run().raised
    assert said in alarm.text
    assert "server may be stuck or stopped" in watchdog.web_push.sent[0].body


def test_a_fresh_heartbeat_and_no_findings_raise_nothing(tmp_path: Path) -> None:
    db_path = _database(tmp_path, heartbeat_at_ms=NOW_MS - CHECKER_HEARTBEAT_STALE_SECONDS * S)
    watchdog = _Watchdog(db_path, tmp_path / "state", _Chief(lambda text: True))
    assert watchdog.run() == WatchdogRun((), (), 0, False)
    assert watchdog.chief.messages == [] and watchdog.web_push.sent == []


def test_a_failing_chief_does_not_stop_the_push(tmp_path: Path) -> None:
    db_path = _database(tmp_path)

    def stuck_server(text: str) -> bool:
        raise httpx.ReadTimeout("the server did not answer in 10 s")

    watchdog = _Watchdog(db_path, tmp_path / "state", _Chief(stuck_server))
    _open_finding(db_path, stalled_since_ms=NOW_MS - 200 * S)
    run = watchdog.run()
    assert run.pushes_delivered == 2 and not run.chief_accepted
    assert len(watchdog.web_push.sent) == len(watchdog.apns.sent) == 1
    assert watchdog.run().raised == ()


def test_the_stall_alarm_switch_stops_the_push_but_not_the_chief(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    conn = connect(db_path)
    try:
        notifications_data.set_preference(conn, "panels", "stall_alarm", False, 1)
    finally:
        conn.close()
    reachable = False
    watchdog = _Watchdog(db_path, tmp_path / "state", _Chief(lambda text: reachable))
    _open_finding(db_path, stalled_since_ms=NOW_MS - 200 * S)

    # Neither channel took it, so the next run tries again rather than losing it.
    assert watchdog.run().pushes_delivered == 0
    assert watchdog.web_push.sent == [] and watchdog.apns.sent == []
    reachable = True
    assert watchdog.run().chief_accepted
    assert len(watchdog.chief.messages) == 2
    assert watchdog.run().raised == ()


def test_the_chief_message_is_send_message_to_the_chief_within_ten_seconds() -> None:
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"fate": "delivered"})

    assert alarms.message_chief(
        "Conversation c1: stalled.",
        server_url="http://127.0.0.1:8767",
        transport=httpx.MockTransport(answer),
    )
    (request,) = requests
    assert (
        request.method == "POST" and str(request.url) == "http://127.0.0.1:8767/api/messages/send"
    )
    assert "x-plan-actor" not in request.headers  # the owner's CLI identity
    assert json.loads(request.content) == {
        "target": {"kind": "chief", "id": "chief"},
        "message": "[Panels watchdog] Conversation c1: stalled.",
        "mode": "steer",
    }
    assert request.extensions["timeout"]["read"] == alarms.CHIEF_MESSAGE_TIMEOUT_SECONDS == 10

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    assert not alarms.message_chief(
        "x", server_url="http://127.0.0.1:8767", transport=httpx.MockTransport(refuse)
    )
