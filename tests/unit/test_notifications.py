from __future__ import annotations

import asyncio
import json
import subprocess
from contextlib import closing
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation import storage as conversation_storage
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import (
    ConversationEventPayload,
    ConversationTurnEnding,
    MessageToOwnerEventPayload,
    PermissionAnsweredEventPayload,
    PermissionAskedEventPayload,
    PromptEventPayload,
    TurnEndedEventPayload,
    UserInputAnsweredEventPayload,
    UserInputFailedEventPayload,
    UserInputRequestedEventPayload,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.core.clock import TestClock as MutableClock
from planner.core.clock import parse_fake_now
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.notifications import attention
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
        brief="Agreed brief.",
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
    conn.execute("UPDATE tickets SET conversation_id = 'c_split' WHERE id = ?", (ticket_id,))


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




def test_conversation_event_and_attention_edge_roll_back_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "atomic-attention.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    _conversation_on_a_ticket(conn, ticket.id)
    conn.close()
    store = ConversationStore(str(db_path), integer_now=lambda: 2)

    def fail_before_commit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced rollback")

    original_commit = conversation_storage._commit_appended_rows
    monkeypatch.setattr(conversation_storage, "_commit_appended_rows", fail_before_commit)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="forced rollback"):
            await store.append_event(
                "c_split",
                MessageToOwnerEventPayload(
                    content=text_message_content("status"),
                    sender=Principal(PrincipalKind.ticket, ticket.id),
                    recipient=OWNER_PRINCIPAL,
                    sender_label=ticket.title,
                ),
            )

    asyncio.run(exercise())
    with connect(str(db_path)) as verify:
        assert verify.execute("SELECT 1 FROM conversation_events").fetchone() is None
        assert (
            verify.execute(
                "SELECT 1 FROM notification_attention_edges "
                "WHERE subject_kind='ticket' AND subject_id=? "
                "AND notification_type='awaiting_reply'",
                (ticket.id,),
            ).fetchone()
            is None
        )

    async def exercise_ending() -> None:
        monkeypatch.setattr(conversation_storage, "_commit_appended_rows", original_commit)
        await store.append_event("c_split", _request_payload(True, "active"))
        monkeypatch.setattr(conversation_storage, "_commit_appended_rows", fail_before_commit)
        ending = (TurnEndedEventPayload(ending=ConversationTurnEnding.interrupted),)
        with pytest.raises(RuntimeError, match="forced rollback"):
            await store.append_turn_ending(
                "c_split", ending, agent_activity=False,
                automatic_compaction_confirmed=False, automatic_compaction_result=None,
            )
        with closing(connect(str(db_path))) as verify:
            assert [row[0] for row in verify.execute(
                "SELECT kind FROM conversation_events ORDER BY sequence"
            )] == ["permission_asked"]
            row = verify.execute(
                "SELECT active,generation FROM notification_attention_state "
                "WHERE subject_id=? AND notification_type='awaiting_answer'", (ticket.id,),
            ).fetchone()
            assert tuple(row) == (1, 1)

        monkeypatch.setattr(conversation_storage, "_commit_appended_rows", original_commit)
        await store.append_turn_ending(
            "c_split", ending, agent_activity=False,
            automatic_compaction_confirmed=False, automatic_compaction_result=None,
        )
        with closing(connect(str(db_path))) as verify:
            assert [row[0] for row in verify.execute(
                "SELECT kind FROM conversation_events ORDER BY sequence"
            )] == ["permission_asked", "turn_ended"]
            row = verify.execute(
                "SELECT active,generation FROM notification_attention_state "
                "WHERE subject_id=? AND notification_type='awaiting_answer'", (ticket.id,),
            ).fetchone()
            assert tuple(row) == (0, 1)
            assert _edges(verify, ticket.id) == [("awaiting_answer", 1)]

    asyncio.run(exercise_ending())


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


def _request_payload(
    permission: bool, request_id: str
) -> PermissionAskedEventPayload | UserInputRequestedEventPayload:
    if permission:
        return PermissionAskedEventPayload(
            ask_id=request_id, title="Allow?", detail=None, options=()
        )
    return UserInputRequestedEventPayload(request_id=request_id, questions=())


def _prompt() -> PromptEventPayload:
    return PromptEventPayload(
        content=text_message_content("continue"),
        sender_label="owner",
        mode=PromptDeliveryMode.queue,
    )


@pytest.mark.parametrize("ending", list(ConversationTurnEnding))
def test_attention_request_lifetime_matches_all_projections(
    tmp_path: Path, ending: ConversationTurnEnding
) -> None:
    with closing(connect(str(tmp_path / "snapshot.db"))) as conn:
        create_schema(conn)
        ticket = _ticket(conn, 1)
        _conversation_on_a_ticket(conn, ticket.id)
        # Historical unrelated kinds remain readable without full payload decoding.
        _conversation_event(conn, 1, "historical_unrelated_kind", "{}")
        conn.execute(
            "UPDATE conversations SET owner_read_through_sequence=1 "
            "WHERE conversation_id='c_split'"
        )
        events: tuple[tuple[ConversationEventPayload, tuple[str, ...], tuple[str, ...]], ...] = (
            (_request_payload(True, "ask-1"), ("ask-1",), ()),
            (_request_payload(True, "ask-2"), ("ask-1", "ask-2"), ()),
            (_request_payload(False, "input-1"), ("ask-1", "ask-2"), ("input-1",)),
            (_request_payload(False, "input-2"), ("ask-1", "ask-2"), ("input-1", "input-2")),
            (PermissionAnsweredEventPayload("unrelated", "yes"),
             ("ask-1", "ask-2"), ("input-1", "input-2")),
            (UserInputAnsweredEventPayload("unrelated", ()),
             ("ask-1", "ask-2"), ("input-1", "input-2")),
            (PermissionAnsweredEventPayload("ask-1", "yes"),
             ("ask-2",), ("input-1", "input-2")),
            (UserInputAnsweredEventPayload("input-2", ()), ("ask-2",), ("input-1",)),
            (_request_payload(False, "input-3"), ("ask-2",), ("input-1", "input-3")),
            (UserInputFailedEventPayload("input-3", "cannot present"), ("ask-2",), ("input-1",)),
            (MessageToOwnerEventPayload(
                content=text_message_content("status"),
                sender=Principal(PrincipalKind.ticket, ticket.id),
                recipient=OWNER_PRINCIPAL,
                sender_label=ticket.title,
            ), ("ask-2",), ("input-1",)),
            (_prompt(), ("ask-2",), ("input-1",)),
            (TurnEndedEventPayload(ending=ending), (), ()),
        )
        sequence = 1
        for payload, permission_ids, input_ids in events:
            sequence += 1
            before = attention.conversation_attention_snapshot(conn, "c_split")
            assert before is not None
            incremental = attention.advance_conversation_attention(
                before, (payload,), owner_read_through_sequence=1, occurred_at=sequence
            )
            _conversation_event(
                conn, sequence, payload.kind, conversation_event_payload_to_canonical_json(payload)
            )
            snapshot = attention.conversation_attention_snapshot(conn, "c_split")
            assert snapshot is not None
            assert snapshot.pending_permission_ask_ids == frozenset(permission_ids)
            assert snapshot.pending_user_input_request_ids == frozenset(input_ids)
            assert incremental == snapshot.attention()
            assert incremental == attention.conversation_attention(conn, "c_split")["c_split"]
            if isinstance(payload, PromptEventPayload):
                sequence += 1
                _conversation_event(conn, sequence, "historical_unrelated_kind", "{}")
                preserved = attention.conversation_attention_snapshot(conn, "c_split")
                assert preserved is not None
                assert preserved.pending_permission_ask_ids == frozenset(permission_ids)
                assert preserved.pending_user_input_request_ids == frozenset(input_ids)
                assert preserved.attention() == attention.conversation_attention(conn)["c_split"]

        assert incremental == attention.ConversationAttention(
            unread_message=True,
            pending_ask=False,
            errored=ending is ConversationTurnEnding.failed,
            latest_sequence=sequence,
            occurred_at=sequence,
        )


@pytest.mark.parametrize("permission", [True, False], ids=["permission", "user-input"])
def test_interrupted_request_allows_the_next_request_to_raise_one_edge(
    tmp_path: Path, permission: bool
) -> None:
    db_path = tmp_path / "request-edges.db"
    with closing(connect(str(db_path))) as conn:
        create_schema(conn)
        ticket = _ticket(conn, 1)
        _conversation_on_a_ticket(conn, ticket.id)
    store = ConversationStore(str(db_path), integer_now=lambda: 2)

    async def exercise() -> None:
        counts: list[int] = []

        def capture_count() -> None:
            with closing(connect(str(db_path))) as conn:
                counts.append(len(_edges(conn, ticket.id)))

        await store.append_delivered_prompt("c_split", prompt=_prompt(), model_change=None)
        capture_count()
        await store.append_event("c_split", _request_payload(permission, "first"))
        capture_count()
        await store.append_turn_ending(
            "c_split", (TurnEndedEventPayload(ending=ConversationTurnEnding.interrupted),),
            agent_activity=False, automatic_compaction_confirmed=False,
            automatic_compaction_result=None,
        )
        capture_count()
        await store.append_delivered_prompt("c_split", prompt=_prompt(), model_change=None)
        capture_count()
        await store.append_event("c_split", _request_payload(permission, "next"))
        capture_count()
        assert counts == [0, 1, 1, 1, 2]
        with closing(connect(str(db_path))) as conn:
            attention.capture_ticket_attention(conn, ticket.id, 3)
            attention.capture_ticket_attention(conn, ticket.id, 4)
            assert _edges(conn, ticket.id) == [("awaiting_answer", 1), ("awaiting_answer", 2)]

    asyncio.run(exercise())


def test_reconciliation_clears_stale_request_without_replaying_its_edge(tmp_path: Path) -> None:
    db_path = tmp_path / "stale-attention.db"
    with closing(connect(str(db_path))) as conn:
        create_schema(conn)
        ticket = _ticket(conn, 1)
        _conversation_on_a_ticket(conn, ticket.id)
        _conversation_event(conn, 1, "permission_asked", '{"ask_id":"old"}')
        attention.capture_ticket_attention(conn, ticket.id, 1)
        # An old writer recorded the ending but retained its active attention state.
        _conversation_event(conn, 2, "turn_ended", '{"ending":"interrupted"}')
        for _ in range(2):
            notifications_data.reconcile_attention(conn)
            row = conn.execute(
                "SELECT active,generation FROM notification_attention_state "
                "WHERE subject_kind='ticket' AND subject_id=? "
                "AND notification_type='awaiting_answer'",
                (ticket.id,),
            ).fetchone()
            assert tuple(row) == (0, 1)
            assert _edges(conn, ticket.id) == [("awaiting_answer", 1)]

    store = ConversationStore(str(db_path), integer_now=lambda: 3)
    asyncio.run(store.append_event("c_split", _request_payload(True, "new")))
    with closing(connect(str(db_path))) as conn:
        notifications_data.reconcile_attention(conn)
        row = conn.execute(
            "SELECT active,generation FROM notification_attention_state "
            "WHERE subject_id=? AND notification_type='awaiting_answer'", (ticket.id,),
        ).fetchone()
        assert tuple(row) == (1, 2)
        assert _edges(conn, ticket.id) == [("awaiting_answer", 1), ("awaiting_answer", 2)]


def test_conversation_binding_and_unbinding_capture_existing_attention(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "binding.db"))
    create_schema(conn)
    ticket = _ticket(conn, 1)
    _conversation_on_a_ticket(conn, ticket.id)
    conn.execute("UPDATE tickets SET conversation_id=NULL WHERE id=?", (ticket.id,))
    _conversation_event(conn, 1, "message_to_owner", "{}")
    conn.commit()
    tickets_data.write_ticket_conversation_start(
        conn,
        ticket.id,
        conversation_id="c_split",
        backend="codex",
        model="test-model",
        reasoning_effort=None,
        now=2,
    )
    assert _edges(conn, ticket.id) == [("awaiting_reply", 1)]
    assert tickets_data.clear_ticket_conversation_link(
        conn,
        ticket.id,
        expected_conversation_id="c_split",
        now=3,
    )
    row = conn.execute(
        "SELECT active FROM notification_attention_state "
        "WHERE subject_id=? AND notification_type='awaiting_reply'",
        (ticket.id,),
    ).fetchone()
    assert row[0] == 0
    tickets_data.delete_ticket(conn, ticket.id, principal=OWNER_PRINCIPAL, now=4)
    for table in ("notification_attention_state", "notification_attention_edges"):
        assert conn.execute(
            f"SELECT 1 FROM {table} WHERE subject_id=?", (ticket.id,)
        ).fetchone() is None
    conn.close()


def test_failed_notification_delivery_retains_one_edge_and_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "failed-delivery.db"
    with closing(connect(str(db_path))) as conn:
        create_schema(conn)
        notifications_data.get_or_create_web_push_identity(conn, 1)
        _subscribe(conn)
        ticket = _ticket(conn, 1)

    class FailedAdapter:
        def send(
            self,
            subscription: PushSubscription,
            intent: NotificationIntent,
            identity: WebPushIdentity,
            *,
            subject: str,
        ) -> WebPushResult:
            return WebPushResult(delivered=False, error="temporary push failure")

    loop = NotificationLoop(
        str(db_path),
        MutableClock(parse_fake_now("2026-09-20T05:00:00+01:00")),
        canonical_origin="https://panels.example",
        adapter=FailedAdapter(),
    )
    assert loop.poll_once() == 1
    assert loop.poll_once() == 0
    with connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT status,attempts,last_error FROM notification_deliveries WHERE subject_id=?",
            (ticket.id,),
        ).fetchone()
        assert tuple(row) == ("retry", 1, "temporary push failure")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM notification_attention_edges WHERE subject_id=?",
                (ticket.id,),
            ).fetchone()[0]
            == 1
        )
