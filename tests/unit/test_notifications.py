from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ResolvedConversationStart,
)
from planner.conversation.events import MessageToOwnerEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.core.clock import TestClock as MutableClock
from planner.core.clock import parse_fake_now
from planner.core.config import load_config
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.notifications import attention as notifications_attention
from planner.notifications import data as notifications_data
from planner.notifications.contracts import (
    NOTIFICATION_SUBJECTS,
    AttentionEdge,
    EdgeKey,
)
from planner.notifications.logic.policy import decide_notification
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, TITLE_MAX_CHARS, Ticket, TicketEdit


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


def _deliveries(conn: Connection, notification_type: str | None = None) -> list[tuple[str, int]]:
    """Which edges reached a device, in order."""
    where = "" if notification_type is None else "WHERE notification_type = ? "
    parameters = () if notification_type is None else (notification_type,)
    return [
        (str(row["notification_type"]), int(row["generation"]))
        for row in conn.execute(
            "SELECT notification_type, generation FROM notification_deliveries "
            f"{where}ORDER BY notification_type, generation",
            parameters,
        )
    ]


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


def test_policy_is_the_one_privacy_safe_edge_to_intent_door() -> None:
    edge = AttentionEdge(
        key=EdgeKey("ticket", "t_example", "awaiting_approval", 1),
        subject=Principal(PrincipalKind.ticket, "t_example"),
        subject_label="Private ticket title",
        occurred_at=1,
    )

    assert decide_notification(edge, enabled=False) is None
    intent = decide_notification(edge, enabled=True)
    assert intent is not None
    assert intent.route == "/#/workspace/t_example"
    assert intent.tag == "panels-ticket-t_example"
    assert "transcript" not in intent.body.lower()


def test_preference_subjects_use_the_shared_principal_kinds() -> None:
    assert {subject.key: subject.principal_kind for subject in NOTIFICATION_SUBJECTS} == {
        "tickets": PrincipalKind.ticket,
        "chief_of_staff": PrincipalKind.chief,
        "sprint_item_supervisors": PrincipalKind.sprint_item,
    }


def test_every_edge_is_decided_once_and_delivered_once(tmp_path: Path) -> None:
    db_path = tmp_path / "notifications.db"
    conn = connect(str(db_path))
    create_schema(conn)
    identity = notifications_data.get_or_create_web_push_identity(conn, 1)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    assert notifications_data.queue_deliveries(conn, 1) == 2
    assert _deliveries(conn) == [("assigned", 1), ("awaiting_approval", 1)]

    tickets_data.mark_ticket_errored(conn, ticket.id, now=2)
    assert notifications_data.queue_deliveries(conn, 2) == 1
    assert notifications_data.queue_deliveries(conn, 2) == 0
    assert len(notifications_data.pending_deliveries(conn, 2)) == 3

    errored = conn.execute(
        "SELECT body, route, tag FROM notification_deliveries "
        "WHERE notification_type = 'errored'"
    ).fetchone()
    assert errored is not None
    assert str(errored["body"]) == "Phone-worthy work has an error."
    assert str(errored["route"]) == f"/#/workspace/{ticket.id}"
    assert str(errored["tag"]) == f"panels-ticket-{ticket.id}"
    conn.close()

    restarted = connect(str(db_path))
    assert notifications_data.get_or_create_web_push_identity(restarted, 3) == identity
    assert notifications_data.queue_deliveries(restarted, 3) == 0
    assert len(notifications_data.pending_deliveries(restarted, 3)) == 3
    restarted.close()


def test_conversation_events_reach_the_catalogue_once(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "conversation-edges.db"))
    create_schema(conn)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_notify', 'codex', '/tmp/workspace', 'full', 4, 1)"
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = 'c_notify' WHERE id = ?",
        (ticket.id,),
    )
    events = (
        (1, "permission_asked", '{"ask_id":"ask_1"}'),
        (2, "user_input_requested", '{"request_id":"request_1"}'),
        (3, "turn_ended", '{"ending":"completed","error_summary":null}'),
        (4, "turn_ended", '{"ending":"failed","error_summary":"stopped"}'),
    )
    conn.executemany(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_notify', ?, ?, ?, 2)",
        events,
    )

    notifications_data.queue_deliveries(conn, 3)
    assert [
        (str(row["notification_type"]), str(row["subject_id"]))
        for row in conn.execute(
            "SELECT notification_type, subject_id FROM notification_deliveries "
            "WHERE notification_type IN ('awaiting_reply','errored') "
            "ORDER BY notification_type"
        )
    ] == [("awaiting_reply", ticket.id), ("errored", ticket.id)]

    assert notifications_data.queue_deliveries(conn, 4) == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 4
    conn.close()


def test_attention_delivers_once_per_rising_edge(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "attention-edges.db"))
    create_schema(conn)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    notifications_data.queue_deliveries(conn, 1)
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_edges', 'codex', '/tmp/workspace', 'full', 3, 1)"
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = 'c_edges' WHERE id = ?",
        (ticket.id,),
    )
    conn.executemany(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_edges', ?, 'message_to_owner', '{}', ?)",
        ((1, 2), (2, 3), (3, 4)),
    )

    notifications_data.queue_deliveries(conn, 2)
    assert _deliveries(conn, "awaiting_reply") == [("awaiting_reply", 1)]
    assert notifications_data.queue_deliveries(conn, 2) == 0

    conn.execute(
        "UPDATE conversations SET owner_read_through_sequence = 3 WHERE conversation_id = 'c_edges'"
    )
    notifications_data.queue_deliveries(conn, 3)
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_edges', 4, 'message_to_owner', '{}', 5)"
    )
    conn.execute("UPDATE conversations SET latest_sequence = 4 WHERE conversation_id = 'c_edges'")
    notifications_data.queue_deliveries(conn, 4)

    assert _deliveries(conn, "awaiting_reply") == [("awaiting_reply", 1), ("awaiting_reply", 2)]
    conn.close()


def test_reply_clear_and_rise_between_projector_polls_keeps_both_edges(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "coalesced-reply-edges.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    notifications_data.queue_deliveries(conn, 1)
    store = ConversationStore(str(db_path), integer_now=lambda: 2)

    async def exercise() -> None:
        await store.create_conversation(
            ResolvedConversationStart(
                conversation_id="c_coalesced",
                backend_key=ConversationBackendKey.codex,
                model="test-model",
                reasoning_effort=None,
                role_materials=None,
                workspace_folder=tmp_path,
                access=ConversationAccess.full,
            )
        )
        tickets_data.write_ticket_conversation_start(
            conn,
            ticket.id,
            conversation_id="c_coalesced",
            backend="codex",
            model="test-model",
            reasoning_effort=None,
            now=2,
        )
        message = MessageToOwnerEventPayload(
            content=text_message_content("status"),
            sender=Principal(PrincipalKind.ticket, ticket.id),
            recipient=OWNER_PRINCIPAL,
            sender_label=ticket.title,
        )
        await store.append_event("c_coalesced", message)
        await store.advance_owner_read_through_sequence("c_coalesced", 1)
        await store.append_event("c_coalesced", message)

    asyncio.run(exercise())
    notifications_data.queue_deliveries(conn, 3)
    assert _deliveries(conn, "awaiting_reply") == [("awaiting_reply", 1), ("awaiting_reply", 2)]
    conn.close()


def test_an_error_notifies_again_only_after_an_explicit_restart_clears_it(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "error-edges.db"))
    create_schema(conn)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    notifications_data.queue_deliveries(conn, 1)

    tickets_data.mark_ticket_errored(conn, ticket.id, now=2)
    tickets_data.clear_ticket_error_for_restart(conn, ticket.id, now=3)
    tickets_data.mark_ticket_errored(conn, ticket.id, now=4)
    notifications_data.queue_deliveries(conn, 5)

    assert _deliveries(conn, "errored") == [("errored", 1), ("errored", 2)]
    conn.close()


def test_assignment_clear_and_rise_between_projector_polls_keeps_both_edges(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "coalesced-assignment-edges.db"))
    create_schema(conn)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    conn.execute(
        "UPDATE tickets SET stage='needs_success', ceiling='needs_success', "
        "pending_proposal=NULL WHERE id=?",
        (ticket.id,),
    )
    notifications_data.queue_deliveries(conn, 1)

    conn.execute(
        "UPDATE tickets SET worker_type='new_worker', stage='needs_understanding' WHERE id=?",
        (ticket.id,),
    )
    notifications_attention.capture_ticket_attention(conn, ticket.id, 2)
    conn.execute(
        "UPDATE tickets SET worker_type='coding', stage='needs_success' WHERE id=?",
        (ticket.id,),
    )
    notifications_attention.capture_ticket_attention(conn, ticket.id, 3)
    conn.execute(
        "UPDATE tickets SET worker_type='new_worker', stage='needs_understanding' WHERE id=?",
        (ticket.id,),
    )
    notifications_attention.capture_ticket_attention(conn, ticket.id, 4)
    notifications_data.queue_deliveries(conn, 5)

    assert _deliveries(conn, "assigned") == [("assigned", 1), ("assigned", 2)]
    conn.close()


def test_permission_and_question_events_share_the_reply_edge(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "ask-edges.db"))
    create_schema(conn)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    notifications_data.queue_deliveries(conn, 1)
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_asks', 'codex', '/tmp/workspace', 'full', 2, 1)"
    )
    conn.execute("UPDATE tickets SET conversation_id = 'c_asks' WHERE id = ?", (ticket.id,))
    conn.executemany(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_asks', ?, 'permission_asked', ?, 2)",
        ((1, '{"ask_id":"a1"}'), (2, '{"ask_id":"a2"}')),
    )
    notifications_data.queue_deliveries(conn, 2)
    assert _deliveries(conn, "awaiting_reply") == [("awaiting_reply", 1)]

    conn.executemany(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_asks', ?, 'permission_answered', ?, 3)",
        ((3, '{"ask_id":"a1"}'), (4, '{"ask_id":"a2"}')),
    )
    conn.execute("UPDATE conversations SET latest_sequence = 4 WHERE conversation_id = 'c_asks'")
    notifications_data.queue_deliveries(conn, 3)
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_asks', 5, 'user_input_requested', '{\"request_id\":\"q1\"}', 4)"
    )
    conn.execute("UPDATE conversations SET latest_sequence = 5 WHERE conversation_id = 'c_asks'")
    notifications_data.queue_deliveries(conn, 4)

    assert _deliveries(conn, "awaiting_reply") == [("awaiting_reply", 1), ("awaiting_reply", 2)]
    conn.close()


def test_not_compacted_maintenance_does_not_notify_a_worker_completion(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "maintenance-conversation-edges.db"))
    create_schema(conn)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_maintenance', 'claude', '/tmp/workspace', 'full', 1, 1)"
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = 'c_maintenance' WHERE id = ?",
        (ticket.id,),
    )
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_maintenance', 1, 'turn_ended', ?, 2)",
        (
            '{"automatic_compaction_result":"not_compacted",'
            '"ending":"completed","error_summary":null}',
        ),
    )

    notifications_data.queue_deliveries(conn, 3)

    assert _deliveries(conn, "errored") == []
    assert _deliveries(conn, "awaiting_reply") == []
    state = conn.execute(
        "SELECT active FROM notification_attention_state "
        "WHERE subject_kind = 'ticket' AND subject_id = ? "
        "AND notification_type = 'errored'",
        (ticket.id,),
    ).fetchone()
    assert state is not None and not bool(state["active"])
    conn.close()


def test_chief_conversation_events_use_agent_destination_and_one_coalescing_tag(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "chief-conversation-edges.db"))
    create_schema(conn)
    _subscribe(conn)
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_chief', 'codex', '/tmp/workspace', 'full', 4, 1)"
    )
    conn.execute(
        "INSERT INTO agents(agent_key, conversation_id) VALUES ('chief_of_staff', 'c_chief')"
    )
    conn.executemany(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_chief', ?, ?, ?, 2)",
        (
            (1, "permission_asked", '{"ask_id":"ask_1"}'),
            (2, "user_input_requested", '{"request_id":"request_1"}'),
            (3, "turn_ended", '{"ending":"completed","error_summary":null}'),
            (4, "turn_ended", '{"ending":"failed","error_summary":"stopped"}'),
        ),
    )

    assert notifications_data.queue_deliveries(conn, 3) == 2
    sent = conn.execute(
        "SELECT subject_kind, subject_id, body, route, tag FROM notification_deliveries "
        "ORDER BY notification_type"
    ).fetchall()
    assert [(row["subject_kind"], row["subject_id"]) for row in sent] == [
        ("agent", "chief_of_staff")
    ] * 2
    assert {str(row["route"]) for row in sent} == {"/#/agents/chief-of-staff"}
    assert {str(row["tag"]) for row in sent} == {"panels-agent-chief_of_staff"}
    assert all(str(row["body"]).startswith("Chief of Staff ") for row in sent)

    assert notifications_data.queue_deliveries(conn, 4) == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 2
    conn.close()


def test_policy_resolves_the_same_type_independently_by_subject(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "subject-policy.db"))
    create_schema(conn)
    _subscribe(conn)
    ticket = _ticket(conn, 1)
    notifications_data.queue_deliveries(conn, 1)
    tickets_data.mark_ticket_errored(conn, ticket.id, now=2)
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_chief_policy', 'codex', '/tmp/workspace', 'full', 1, 1)"
    )
    conn.execute(
        "INSERT INTO agents(agent_key, conversation_id) VALUES ('chief_of_staff', 'c_chief_policy')"
    )
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_chief_policy', 1, 'turn_ended', "
        '\'{"ending":"failed","error_summary":"stopped"}\', 2)'
    )
    notifications_data.set_preference(conn, "tickets", "errored", False, 2)

    assert notifications_data.queue_deliveries(conn, 3) == 2
    assert [
        str(row["subject_kind"])
        for row in conn.execute(
            "SELECT subject_kind FROM notification_deliveries "
            "WHERE notification_type = 'errored' ORDER BY subject_kind"
        )
    ] == ["agent"]
    conn.close()


def test_an_edge_outside_the_closed_subject_vocabulary_cannot_be_written(
    tmp_path: Path,
) -> None:
    """What used to be a legacy fact the policy had to suppress is now unwritable."""
    conn = connect(str(tmp_path / "closed-vocabulary.db"))
    create_schema(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO notification_attention_edges"
            "(subject_kind, subject_id, notification_type, generation, occurred_at) "
            "VALUES ('reviewer', 'reviewer', 'errored', 1, 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO notification_attention_edges"
            "(subject_kind, subject_id, notification_type, generation, occurred_at) "
            "VALUES ('ticket', 't_example', 'sprint_item_ping', 1, 1)"
        )
    conn.close()


def test_nothing_outlives_its_subject(tmp_path: Path) -> None:
    """A deleted subject used to take its rows by foreign key. The queue step does it now.

    The delivery log holds no foreign key to a Ticket, an Item or an agent, because it
    is keyed by the edge. So the same pass that reconciles state deletes every state
    row, edge and delivery whose subject is gone.
    """
    conn = connect(str(tmp_path / "notification-subject-integrity.db"))
    create_schema(conn)
    _subscribe(conn, name="subject-integrity")
    ticket = _ticket(conn, 1)
    assert notifications_data.queue_deliveries(conn, 1) == 2
    tickets_data.mark_ticket_errored(conn, ticket.id, now=2)
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_agent_integrity', 'codex', '/tmp/workspace', 'full', 1, 1)"
    )
    conn.execute(
        "INSERT INTO agents(agent_key, conversation_id) "
        "VALUES ('chief_of_staff', 'c_agent_integrity')"
    )
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_agent_integrity', 1, 'turn_ended', "
        '\'{"ending":"failed","error_summary":"stopped"}\', 2)'
    )

    assert notifications_data.queue_deliveries(conn, 3) == 2
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 4

    conn.execute("DELETE FROM tickets WHERE id = ?", (ticket.id,))
    notifications_data.queue_deliveries(conn, 4)
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM notification_attention_edges").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM notification_attention_state").fetchone()[0] == 2

    conn.execute("DELETE FROM agents WHERE agent_key = 'chief_of_staff'")
    notifications_data.queue_deliveries(conn, 5)
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_attention_edges").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_attention_state").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_a_removed_device_takes_its_deliveries(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "removed-device.db"))
    create_schema(conn)
    subscription_id = _subscribe(conn, name="removed-device")
    _ticket(conn, 1)
    assert notifications_data.queue_deliveries(conn, 1) == 2
    assert notifications_data.remove_subscription(conn, subscription_id) is True
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 0
    conn.close()


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


def test_an_awaiting_approval_edge_is_dated_from_when_the_proposal_parked(
    tmp_path: Path,
) -> None:
    """The wait is the proposal's own age, not the last time control moved.

    A Ticket's status is no longer stored, so there is no status-change stamp for the
    projector to read when it has to work a rising edge out for itself. The parked
    proposal knows when it was parked, and that is the same number the review list
    already shows as the wait.
    """
    conn = connect(str(tmp_path / "parked.db"))
    create_schema(conn)
    ticket = _ticket(conn, 100)
    # Settle the kickoff the creation parked, so the next proposal is the one under test.
    tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        principal=OWNER_PRINCIPAL,
        now=200,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    # Filed without a claim out, so nothing moves the claim's own timestamp: it still
    # says 100, the moment the Ticket was created.
    tickets_data.file_current_proposal(
        conn,
        ticket.id,
        body="What done means",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=900,
    )
    assert (
        int(
            conn.execute(
                "SELECT worker_step_claim_changed_at FROM tickets WHERE id = ?",
                (ticket.id,),
            ).fetchone()[0]
        )
        == 100
    )
    # Something unrelated touches the row afterwards, so `updated_at` is not the answer
    # either.
    tickets_data.edit_ticket(
        conn,
        ticket.id,
        edit=TicketEdit(title="Phone-worthy work, retitled"),
        principal=OWNER_PRINCIPAL,
        now=1_500,
        title_max_chars=TITLE_MAX_CHARS,
    )
    # Make the queue step find the rising edge itself, the way it does after a restart.
    conn.execute("DELETE FROM notification_attention_state WHERE subject_id = ?", (ticket.id,))
    conn.execute("DELETE FROM notification_attention_edges WHERE subject_id = ?", (ticket.id,))

    notifications_data.queue_deliveries(conn, 2_000)

    edge = conn.execute(
        "SELECT occurred_at FROM notification_attention_edges "
        "WHERE subject_id = ? AND notification_type = 'awaiting_approval'",
        (ticket.id,),
    ).fetchone()
    assert edge is not None
    assert int(edge["occurred_at"]) == 900
    conn.close()
