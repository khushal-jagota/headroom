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
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.notifications import attention
from planner.notifications import data as notifications_data
from planner.notifications.contracts import (
    AttentionEdge,
    EdgeKey,
    NotificationIntent,
    PushSubscription,
    WebPushIdentity,
    WebPushResult,
)
from planner.notifications.logic.policy import decide_notification
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
        kickoff_note="Agreed brief.",
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
        assert sum(len(subject["types"]) for subject in payload["subjects"]) == 11
        assert types_by_subject["tickets"] == {
            "awaiting_reply",
            "awaiting_answer",
            "awaiting_approval",
            "assigned",
            "errored",
        }
        assert types_by_subject["chief_of_staff"] == {
            "awaiting_reply",
            "awaiting_answer",
            "errored",
        }
        assert types_by_subject["sprint_item_supervisors"] == {
            "awaiting_reply",
            "awaiting_answer",
            "errored",
        }
        enabled_by_subject = {
            subject["key"]: {item["id"]: item["enabled"] for item in subject["types"]}
            for subject in payload["subjects"]
        }
        assert enabled_by_subject["sprint_item_supervisors"] == {
            "awaiting_reply": True,
            "awaiting_answer": True,
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
    # One Ticket at a worker-owned Brief earns exactly one push. Every push about a
    # Ticket carries that Ticket's id as its OS tag, so a second one would replace the
    # first on the phone and the approval would never be read.
    assert loop.poll_once() == 1
    assert [subscription for subscription, _ in sent] == [subscription_id]
    assert [intent.body for _, intent in sent] == ["Phone-worthy work needs your approval."]
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
        ] == [("awaiting_approval", "delivered", 1)]


def _conversation_on_a_ticket(conn: Connection, ticket_id: str) -> None:
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_split', 'codex', '/tmp/workspace', 'full', 0, 1)"
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = 'c_split' WHERE id = ?", (ticket_id,)
    )


def _conversation_event(conn: Connection, sequence: int, kind: str, payload: str) -> None:
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) VALUES ('c_split', ?, ?, ?, ?)",
        (sequence, kind, payload, sequence),
    )
    conn.execute(
        "UPDATE conversations SET latest_sequence = ? WHERE conversation_id = 'c_split'",
        (sequence,),
    )


# The conversation raises two of a Ticket's five signals. The other three come from the
# Ticket's own stage and proposal, and say nothing about this split.
_FROM_THE_CONVERSATION = ("awaiting_reply", "awaiting_answer")


def _edges(conn: Connection, ticket_id: str) -> list[tuple[str, int]]:
    return [
        (str(row["notification_type"]), int(row["generation"]))
        for row in conn.execute(
            "SELECT notification_type, generation FROM notification_attention_edges "
            "WHERE subject_kind='ticket' AND subject_id=? "
            "AND notification_type IN ('awaiting_reply','awaiting_answer') "
            "ORDER BY notification_type, generation",
            (ticket_id,),
        )
    ]


def test_a_message_that_arrives_during_an_ask_still_raises_its_own_edge(
    tmp_path: Path,
) -> None:
    """The ask and the message are two things. One flag could only carry one of them.

    With both OR-ed into `awaiting_reply`, the flag was already true when the message
    landed, so no rising edge was written and the message notified nobody at all.
    """
    conn = connect(str(tmp_path / "split.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    _conversation_on_a_ticket(conn, ticket.id)

    _conversation_event(conn, 1, "permission_asked", json.dumps({"ask_id": "ask-1"}))
    attention.capture_ticket_attention(conn, ticket.id, 1)
    _conversation_event(conn, 2, "message_to_owner", "{}")
    attention.capture_ticket_attention(conn, ticket.id, 2)

    assert _edges(conn, ticket.id) == [("awaiting_answer", 1), ("awaiting_reply", 1)]
    conn.close()


def test_a_pending_ask_says_it_is_an_ask_and_not_a_message(tmp_path: Path) -> None:
    """An ask told Khushal the Ticket had a message for him, which it did not."""
    conn = connect(str(tmp_path / "ask.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    _conversation_on_a_ticket(conn, ticket.id)

    _conversation_event(conn, 1, "user_input_requested", json.dumps({"request_id": "q-1"}))
    attention.capture_ticket_attention(conn, ticket.id, 1)

    assert [kind for kind, _ in _edges(conn, ticket.id)] == ["awaiting_answer"]
    intent = decide_notification(
        AttentionEdge(
            key=EdgeKey(
                subject_kind="ticket",
                subject_id=ticket.id,
                notification_type="awaiting_answer",
                generation=1,
            ),
            subject=Principal(PrincipalKind.ticket, ticket.id),
            subject_label="Phone-worthy work",
            occurred_at=1,
        ),
        enabled=True,
    )
    assert intent is not None
    assert intent.body == "Phone-worthy work is waiting on your answer."
    conn.close()


def test_an_answered_ask_clears_the_flag_without_another_notification(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "answered.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    _conversation_on_a_ticket(conn, ticket.id)

    _conversation_event(conn, 1, "permission_asked", json.dumps({"ask_id": "ask-1"}))
    attention.capture_ticket_attention(conn, ticket.id, 1)
    _conversation_event(conn, 2, "permission_answered", json.dumps({"ask_id": "ask-1"}))
    attention.capture_ticket_attention(conn, ticket.id, 2)

    assert [kind for kind, _ in _edges(conn, ticket.id)] == ["awaiting_answer"]
    assert [
        (str(row["notification_type"]), bool(row["active"]))
        for row in conn.execute(
            "SELECT notification_type, active FROM notification_attention_state "
            "WHERE subject_kind='ticket' AND subject_id=? AND notification_type='awaiting_answer'",
            (ticket.id,),
        )
    ] == [("awaiting_answer", False)]
    conn.close()
