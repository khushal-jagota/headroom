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
from planner.notifications import data as notifications_data
from planner.notifications.contracts import NOTIFICATION_SUBJECTS, NotificationFact
from planner.notifications.logic.policy import decide_notification
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, StageOwnershipMode, Ticket


def _ticket(conn: Connection, now: int) -> Ticket:
    return tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Phone-worthy work",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
    )


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


def test_policy_is_the_one_privacy_safe_fact_to_intent_door() -> None:
    fact = NotificationFact(
        fact_id="ticket:t_example:1",
        notification_type="awaiting_approval",
        subject=Principal(PrincipalKind.ticket, "t_example"),
        subject_label="Private ticket title",
        occurred_at=1,
    )

    assert decide_notification(fact, enabled=False) is None
    intent = decide_notification(fact, enabled=True)
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


def test_status_projection_policy_and_delivery_are_exact_once(tmp_path: Path) -> None:
    db_path = tmp_path / "notifications.db"
    conn = connect(str(db_path))
    create_schema(conn)
    identity = notifications_data.get_or_create_web_push_identity(conn, 1)
    ticket = _ticket(conn, 1)
    notifications_data.project_facts(conn)
    assert notifications_data.apply_policy(conn, 1) == 2
    notifications_data.register_subscription(
        conn,
        endpoint="https://push.example/subscription",
        p256dh="p256dh-value",
        auth="auth-value",
        now=1,
    )

    tickets_data.mark_ticket_errored(conn, ticket.id, error="backend stopped", now=2)
    notifications_data.project_facts(conn)
    assert notifications_data.apply_policy(conn, 2) == 1
    assert notifications_data.apply_policy(conn, 2) == 0
    assert len(notifications_data.pending_deliveries(conn, 2)) == 1

    fact = conn.execute(
        "SELECT notification_type, payload FROM notification_facts "
        "WHERE notification_type = 'errored'"
    ).fetchone()
    assert fact is not None
    assert fact["notification_type"] == "errored"
    assert json.loads(fact["payload"]) == {"subject_label": "Phone-worthy work"}
    assert conn.execute("SELECT COUNT(*) FROM notification_decisions").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM notification_intents").fetchone()[0] == 3
    conn.close()

    restarted = connect(str(db_path))
    assert notifications_data.get_or_create_web_push_identity(restarted, 3) == identity
    notifications_data.project_facts(restarted)
    assert notifications_data.apply_policy(restarted, 3) == 0
    assert len(notifications_data.pending_deliveries(restarted, 3)) == 1
    restarted.close()


def test_needs_approval_fact_includes_failed_holder_alerts_until_resolved(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "holder-notifications.db"))
    create_schema(conn)
    holder_ticket = _ticket(conn, 1)
    holder_item = sprints_data.create_item(
        conn,
        title="Proposal holder",
        project_id="project_vylo",
        clock=MutableClock(parse_fake_now("2026-09-15T12:00:00+02:00")),
    )
    holders = (
        OWNER_PRINCIPAL,
        Principal(PrincipalKind.chief, "chief"),
        Principal(PrincipalKind.sprint_item, holder_item.id),
        Principal(PrincipalKind.ticket, holder_ticket.id),
    )
    tickets = [_ticket(conn, index + 2) for index in range(len(holders))]
    for index, (ticket, holder) in enumerate(zip(tickets, holders, strict=True), start=1):
        conn.execute(
            "UPDATE tickets SET ceiling_holder = ?, ticket_status = 'awaiting_approval', "
            "ticket_status_revision = 1, ticket_status_changed_at = ? WHERE id = ?",
            (
                json.dumps({"kind": holder.kind.value, "id": holder.id}),
                index,
                ticket.id,
            ),
        )

    surfaced = tickets[1]
    conn.execute(
        "INSERT INTO proposal_delivery_failures "
        "(ticket_id,proposal_generation,conversation_id,attempt_count,last_error,"
        "visibility_message_id,created_at,resolved_at) "
        "VALUES (?,1,NULL,10,'write_to_backend_failed',?,20,NULL)",
        (surfaced.id, f"proposal-delivery-failed:{surfaced.id}:1"),
    )

    notifications_data.project_facts(conn)

    rows = conn.execute(
        "SELECT source_id, notification_type FROM notification_facts "
        "WHERE notification_type = 'awaiting_approval' ORDER BY source_id"
    ).fetchall()
    assert {(row["source_id"], row["notification_type"]) for row in rows} == {
        (f"ticket:{holder_ticket.id}:awaiting_approval", "awaiting_approval"),
        (f"ticket:{tickets[0].id}:awaiting_approval", "awaiting_approval"),
        (f"ticket:{surfaced.id}:awaiting_approval", "awaiting_approval"),
    }
    states = conn.execute(
        "SELECT subject_id, active FROM notification_attention_state "
        "WHERE notification_type = 'awaiting_approval' AND subject_id IN (?, ?, ?, ?)",
        tuple(ticket.id for ticket in tickets),
    ).fetchall()
    assert {str(row["subject_id"]): bool(row["active"]) for row in states} == {
        ticket.id: ticket.id in {tickets[0].id, surfaced.id} for ticket in tickets
    }

    conn.execute(
        "UPDATE proposal_delivery_failures SET resolved_at=21 WHERE ticket_id=?",
        (surfaced.id,),
    )
    notifications_data.project_facts(conn)
    state = conn.execute(
        "SELECT active FROM notification_attention_state "
        "WHERE subject_id=? AND notification_type='awaiting_approval'",
        (surfaced.id,),
    ).fetchone()
    assert state is not None and not bool(state["active"])
    conn.close()


def test_conversation_events_project_to_the_catalogue_once(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "conversation-facts.db"))
    create_schema(conn)
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

    notifications_data.project_facts(conn)
    assert [
        (str(row["fact_id"]), str(row["notification_type"]))
        for row in conn.execute(
            "SELECT fact_id, notification_type FROM notification_facts "
            "WHERE notification_type IN ('awaiting_reply','errored') "
            "ORDER BY notification_type"
        )
    ] == [
        (f"attention:ticket:{ticket.id}:awaiting_reply:1", "awaiting_reply"),
        (f"attention:ticket:{ticket.id}:errored:1", "errored"),
    ]

    notifications_data.project_facts(conn)
    assert conn.execute("SELECT COUNT(*) FROM notification_facts").fetchone()[0] == 4
    conn.close()


def test_attention_facts_emit_once_per_rising_edge(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "attention-edges.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    notifications_data.project_facts(conn)
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

    notifications_data.project_facts(conn)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM notification_facts WHERE notification_type = 'awaiting_reply'"
        ).fetchone()[0]
        == 1
    )
    assert notifications_data.project_facts(conn) == 0

    conn.execute(
        "UPDATE conversations SET owner_read_through_sequence = 3 WHERE conversation_id = 'c_edges'"
    )
    notifications_data.project_facts(conn)
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_edges', 4, 'message_to_owner', '{}', 5)"
    )
    conn.execute("UPDATE conversations SET latest_sequence = 4 WHERE conversation_id = 'c_edges'")
    notifications_data.project_facts(conn)

    assert [
        str(row["fact_id"])
        for row in conn.execute(
            "SELECT fact_id FROM notification_facts "
            "WHERE notification_type = 'awaiting_reply' ORDER BY source_sequence"
        )
    ] == [
        f"attention:ticket:{ticket.id}:awaiting_reply:1",
        f"attention:ticket:{ticket.id}:awaiting_reply:2",
    ]
    conn.close()


def test_reply_clear_and_rise_between_projector_polls_keeps_both_edges(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "coalesced-reply-edges.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    notifications_data.project_facts(conn)
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
    notifications_data.project_facts(conn)
    facts = conn.execute(
        "SELECT fact_id FROM notification_facts WHERE notification_type='awaiting_reply' "
        "ORDER BY source_sequence"
    ).fetchall()
    assert [str(row["fact_id"]) for row in facts] == [
        f"attention:ticket:{ticket.id}:awaiting_reply:1",
        f"attention:ticket:{ticket.id}:awaiting_reply:2",
    ]
    conn.close()


def test_error_fact_repeats_only_after_explicit_restart_clears_it(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "error-edges.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    notifications_data.project_facts(conn)

    tickets_data.mark_ticket_errored(conn, ticket.id, error="first", now=2)
    tickets_data.clear_ticket_error_for_restart(conn, ticket.id, now=3)
    tickets_data.mark_ticket_errored(conn, ticket.id, error="second", now=4)
    notifications_data.project_facts(conn)

    assert [
        str(row["fact_id"])
        for row in conn.execute(
            "SELECT fact_id FROM notification_facts "
            "WHERE notification_type = 'errored' ORDER BY source_sequence"
        )
    ] == [
        f"attention:ticket:{ticket.id}:errored:1",
        f"attention:ticket:{ticket.id}:errored:2",
    ]
    conn.close()


def test_assignment_clear_and_rise_between_projector_polls_keeps_both_edges(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "coalesced-assignment-edges.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    conn.execute(
        "UPDATE tickets SET stage='needs_success', ceiling='needs_success', "
        "default_stage_ownership_mode='worker', stage_ownership_overrides='{}', "
        "pending_proposal=NULL WHERE id=?",
        (ticket.id,),
    )
    notifications_data.project_facts(conn)

    tickets_data.set_stage_ownership(
        conn,
        ticket.id,
        stage="needs_success",
        ownership_mode=StageOwnershipMode.user,
        now=2,
    )
    tickets_data.set_stage_ownership(
        conn,
        ticket.id,
        stage="needs_success",
        ownership_mode=StageOwnershipMode.worker,
        now=3,
    )
    tickets_data.set_stage_ownership(
        conn,
        ticket.id,
        stage="needs_success",
        ownership_mode=StageOwnershipMode.user,
        now=4,
    )
    notifications_data.project_facts(conn)

    facts = conn.execute(
        "SELECT fact_id FROM notification_facts WHERE notification_type='assigned' "
        "ORDER BY source_sequence"
    ).fetchall()
    assert [str(row["fact_id"]) for row in facts] == [
        f"attention:ticket:{ticket.id}:assigned:1",
        f"attention:ticket:{ticket.id}:assigned:2",
    ]
    conn.close()


def test_permission_and_question_events_share_the_reply_edge(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "ask-edges.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    notifications_data.project_facts(conn)
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
    notifications_data.project_facts(conn)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM notification_facts WHERE notification_type = 'awaiting_reply'"
        ).fetchone()[0]
        == 1
    )

    conn.executemany(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_asks', ?, 'permission_answered', ?, 3)",
        ((3, '{"ask_id":"a1"}'), (4, '{"ask_id":"a2"}')),
    )
    conn.execute("UPDATE conversations SET latest_sequence = 4 WHERE conversation_id = 'c_asks'")
    notifications_data.project_facts(conn)
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_asks', 5, 'user_input_requested', '{\"request_id\":\"q1\"}', 4)"
    )
    conn.execute("UPDATE conversations SET latest_sequence = 5 WHERE conversation_id = 'c_asks'")
    notifications_data.project_facts(conn)

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM notification_facts WHERE notification_type = 'awaiting_reply'"
        ).fetchone()[0]
        == 2
    )
    conn.close()


def test_not_compacted_maintenance_does_not_project_a_worker_completion(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "maintenance-conversation-facts.db"))
    create_schema(conn)
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

    notifications_data.project_facts(conn)

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM notification_facts WHERE source_kind = 'conversation'"
        ).fetchone()[0]
        == 0
    )
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
    conn = connect(str(tmp_path / "chief-conversation-facts.db"))
    create_schema(conn)
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

    notifications_data.project_facts(conn)
    assert notifications_data.apply_policy(conn, 3) == 2
    facts = conn.execute(
        "SELECT subject_kind, COALESCE(ticket_id, agent_key) AS subject_id, payload "
        "FROM notification_facts ORDER BY source_sequence"
    ).fetchall()
    assert [(row["subject_kind"], row["subject_id"]) for row in facts] == [
        ("agent", "chief_of_staff")
    ] * 2
    assert all(json.loads(row["payload"]) == {"subject_label": "Chief of Staff"} for row in facts)
    intents = conn.execute(
        "SELECT body, route, tag FROM notification_intents ORDER BY fact_id"
    ).fetchall()
    assert len(intents) == 2
    assert {row["route"] for row in intents} == {"/#/agents/chief-of-staff"}
    assert {row["tag"] for row in intents} == {"panels-agent-chief_of_staff"}
    assert all(str(row["body"]).startswith("Chief of Staff ") for row in intents)

    notifications_data.project_facts(conn)
    assert notifications_data.apply_policy(conn, 4) == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_facts").fetchone()[0] == 2
    conn.close()


def test_policy_resolves_the_same_type_independently_by_subject(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "subject-policy.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    notifications_data.project_facts(conn)
    tickets_data.mark_ticket_errored(conn, ticket.id, error="stopped", now=2)
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

    notifications_data.project_facts(conn)
    assert notifications_data.apply_policy(conn, 3) == 4
    assert [
        (row["subject_kind"], row["outcome"])
        for row in conn.execute(
            "SELECT f.subject_kind, d.outcome FROM notification_facts f "
            "JOIN notification_decisions d ON d.fact_id = f.fact_id "
            "WHERE f.notification_type = 'errored' "
            "ORDER BY f.subject_kind"
        )
    ] == [("agent", "notify"), ("ticket", "suppress")]
    conn.close()


def test_policy_suppresses_a_legacy_arbitrary_agent_fact_and_continues(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "legacy-agent-fact.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    notifications_data.project_facts(conn)
    tickets_data.mark_ticket_errored(conn, ticket.id, error="stopped", now=2)
    notifications_data.project_facts(conn)
    conn.execute("INSERT INTO agents(agent_key) VALUES ('reviewer')")
    conn.execute(
        "INSERT INTO notification_facts"
        "(fact_id, notification_type, subject_kind, agent_key, source_kind, "
        "source_id, source_sequence, occurred_at, payload) "
        "VALUES ('legacy:reviewer:1', 'errored', 'agent', 'reviewer', "
        "'conversation', 'c_legacy', 1, 1, '{\"subject_label\":\"Reviewer\"}')"
    )

    assert notifications_data.apply_policy(conn, 3) == 4
    assert [
        (str(row["fact_id"]), str(row["outcome"]))
        for row in conn.execute(
            "SELECT fact_id, outcome FROM notification_decisions ORDER BY fact_id"
        )
    ] == [
        (f"attention:ticket:{ticket.id}:assigned:1", "notify"),
        (f"attention:ticket:{ticket.id}:awaiting_approval:1", "notify"),
        (f"attention:ticket:{ticket.id}:errored:1", "notify"),
        ("legacy:reviewer:1", "suppress"),
    ]
    assert conn.execute("SELECT COUNT(*) FROM notification_intents").fetchone()[0] == 3
    conn.close()


def test_typed_subject_foreign_keys_reject_invalid_rows_and_cascade_full_graph(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "notification-subject-integrity.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    notifications_data.project_facts(conn)
    notifications_data.register_subscription(
        conn,
        endpoint="https://push.example/subject-integrity",
        p256dh="p256dh-value",
        auth="auth-value",
        now=1,
    )
    tickets_data.mark_ticket_errored(conn, ticket.id, error="stopped", now=2)
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

    notifications_data.project_facts(conn)
    assert notifications_data.apply_policy(conn, 3) == 4
    assert conn.execute("SELECT COUNT(*) FROM notification_facts").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM notification_decisions").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM notification_intents").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 4

    invalid_fact_sql = (
        "INSERT INTO notification_facts"
        "(fact_id, notification_type, subject_kind, ticket_id, agent_key, source_kind, "
        "source_id, source_sequence, occurred_at, payload) "
        "VALUES (?, 'errored', ?, ?, ?, 'ticket', ?, 99, 3, '{}')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            invalid_fact_sql,
            ("invalid:missing", "ticket", "t_missing", None, "missing"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            invalid_fact_sql,
            ("invalid:missing-agent", "agent", None, "agent_missing", "missing"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            invalid_fact_sql,
            ("invalid:mismatched", "agent", ticket.id, None, ticket.id),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            invalid_fact_sql,
            (
                "invalid:both",
                "ticket",
                ticket.id,
                "chief_of_staff",
                ticket.id,
            ),
        )

    conn.execute("DELETE FROM tickets WHERE id = ?", (ticket.id,))
    assert conn.execute("SELECT COUNT(*) FROM notification_facts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM notification_decisions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM notification_intents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 1

    conn.execute("DELETE FROM agents WHERE agent_key = 'chief_of_staff'")
    assert conn.execute("SELECT COUNT(*) FROM notification_facts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_decisions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_intents").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM notification_deliveries").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
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
        assert types_by_subject["sprint_item_supervisors"] == {"awaiting_reply", "errored"}
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
