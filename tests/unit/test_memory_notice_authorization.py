"""Caller permission proofs. Injected Uncertain is proposed-contract evidence only."""

from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from tests.unit.test_worker_step_readiness_loop import _World

from planner.conversation.contracts import (
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryUncertain,
)
from planner.core.db import connect
from planner.runtime import conversation_start, worker_memory
from planner.runtime.worker_memory_contracts import MemoryNoticeEligibility
from planner.runtime.worker_step_readiness_loop import tell_a_worker_it_lost_its_memory
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture(autouse=True)
def credential_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Any credential helper access is an isolation failure."""
    from planner.conversation.backends.codex_app_server import login

    def forbidden_credentials(*args: object, **kwargs: object) -> None:
        raise AssertionError("credential helper access is forbidden")

    monkeypatch.setattr(login, "_credentials", forbidden_credentials)


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[_World, str]:
    world = _World(tmp_path)
    ticket_id = world.ready_ticket(conversation_id="synthetic-memory-conversation")
    world.start_conversation("synthetic-memory-conversation")
    assert world.start_step(ticket_id).started
    world.conversations.complete_running_turn("synthetic-memory-conversation")
    world.record_compacted_conversation(
        "synthetic-memory-conversation", last_worker_step_sequence=4, compacted_through=9
    )
    return world, ticket_id


def _notice(world: _World, ticket_id: str) -> bool:
    return asyncio.run(
        tell_a_worker_it_lost_its_memory(
            ticket_id,
            connect_database=world.connect,
            conversation_system=world.conversations,
            worker_type_registry=configured_worker_type_registry(),
            now=lambda: 1,
        )
    )


def test_competing_connections_consume_exactly_once(prepared: tuple[_World, str]) -> None:
    world, ticket_id = prepared
    with world.connect() as conn:
        key = worker_memory.current_memory_notice_key(conn, ticket_id)
    assert key is not None

    def acquire(_: int) -> bool:
        # The canonical consumer owns BEGIN/COMMIT, not connection.__enter__.
        with closing(world.connect()) as conn:
            return (
                worker_memory.consume_memory_notice_authorization(conn, key, now=1).reservation
                is not None
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(acquire, range(2))) == [False, True]
    with world.connect() as conn:
        inspection = worker_memory.inspect_memory_notices(conn, ticket_id)
        assert len(inspection.reservations) == 1
        assert inspection.evidence == ()
        assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, key.conversation_id)
    # Simulate a crash before send, then reopen: consumption is enough to suppress.
    assert not _notice(world, ticket_id)


@pytest.mark.parametrize(
    "outcome", ["refused", "uncertain", "exception", "cancelled", "effect_then_crash"]
)
def test_every_outcome_retains_consumption(
    prepared: tuple[_World, str],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    world, ticket_id = prepared
    calls: list[str] = []

    async def send(*args: object, **kwargs: object) -> conversation_start.DeliveredMessage:
        calls.append(str(kwargs["sender_message_id"]))
        conn = args[1]
        assert isinstance(conn, sqlite3.Connection) and not conn.in_transaction
        with world.connect() as reopened:
            assert len(worker_memory.inspect_memory_notices(reopened, ticket_id).reservations) == 1
        if outcome == "exception":
            raise RuntimeError("synthetic failure before receipt")
        if outcome in {"cancelled", "effect_then_crash"}:
            raise asyncio.CancelledError("synthetic crash window")
        fate = (
            PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
            )
            if outcome == "refused"
            else PromptDeliveryUncertain()
        )
        return conversation_start.DeliveredMessage(
            conversation_id="synthetic-memory-conversation", fate=fate
        )

    monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", send)
    if outcome in {"cancelled", "effect_then_crash"}:
        with pytest.raises(asyncio.CancelledError):
            _notice(world, ticket_id)
    else:
        _notice(world, ticket_id)
    # Reopen and a changed title/stage cannot create automatic permission.
    with world.connect() as conn:
        conn.execute(
            "UPDATE tickets SET title='Changed', stage='needs_implementation' WHERE id=?",
            (ticket_id,),
        )
    assert not _notice(world, ticket_id)
    assert len(calls) == 1
    with world.connect() as conn:
        inspection = worker_memory.inspect_memory_notices(conn, ticket_id)
        assert inspection.reservations[0].sender_message_id == calls[0]
        assert inspection.evidence == ()
        assert worker_memory.conversation_holds_an_unanswered_memory_loss(
            conn, "synthetic-memory-conversation"
        )


@pytest.mark.parametrize("failure_sql", ["BEGIN IMMEDIATE", "COMMIT"])
def test_acquisition_or_commit_failure_sends_nothing(
    prepared: tuple[_World, str],
    monkeypatch: pytest.MonkeyPatch,
    failure_sql: str,
) -> None:
    world, ticket_id = prepared
    calls: list[object] = []

    class FailedConnection(sqlite3.Connection):
        def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
            if sql == failure_sql:
                raise sqlite3.OperationalError("synthetic transaction failure")
            return super().execute(sql, *args, **kwargs)  # type: ignore[arg-type]

    def failing_connect() -> sqlite3.Connection:
        conn = sqlite3.connect(world.db_path, factory=FailedConnection, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    async def send(*args: object, **kwargs: object) -> None:
        calls.append(kwargs)
        raise AssertionError("send after failed transaction")

    monkeypatch.setattr(world, "connect", failing_connect)
    monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", send)
    assert not _notice(world, ticket_id)
    assert calls == []
    with connect(world.db_path) as conn:
        assert worker_memory.inspect_memory_notices(conn, ticket_id).reservations == ()


def test_watermark_mutation_is_same_key_but_new_event_has_permission(
    prepared: tuple[_World, str],
) -> None:
    world, ticket_id = prepared
    with closing(world.connect()) as conn:
        key = worker_memory.current_memory_notice_key(conn, ticket_id)
        assert key is not None
        first = worker_memory.consume_memory_notice_authorization(conn, key, now=1)
        conn.execute("UPDATE conversations SET automatically_compacted_through_sequence=15")
        assert worker_memory.current_memory_notice_key(conn, ticket_id) == key
        assert (
            worker_memory.memory_notice_eligibility(conn, key) is MemoryNoticeEligibility.consumed
        )
        conn.execute(
            "INSERT INTO conversation_events VALUES (?,16,'context_compacted','{}',1)",
            (key.conversation_id,),
        )
        new_key = worker_memory.current_memory_notice_key(conn, ticket_id)
        assert new_key is not None and new_key != key
        second = worker_memory.consume_memory_notice_authorization(conn, new_key, now=2)
        assert first.reservation and second.reservation
        assert first.reservation.sender_message_id != second.reservation.sender_message_id


def test_conversation_hold_blocks_new_events_and_inspection_changes_no_rows(
    prepared: tuple[_World, str],
) -> None:
    world, ticket_id = prepared
    with world.connect() as conn:
        key = worker_memory.current_memory_notice_key(conn, ticket_id)
        assert key is not None
        conn.execute(
            "INSERT INTO worker_memory_notice_holds "
            "VALUES (?,?,NULL,1,'legacy_unresolved_boundary')",
            (ticket_id, key.conversation_id),
        )
        conn.execute(
            "INSERT INTO conversation_events VALUES (?,16,'context_compacted','{}',1)",
            (key.conversation_id,),
        )
        before = conn.total_changes
        inspection = worker_memory.inspect_memory_notices(conn, ticket_id)
        assert inspection.holds[0].context_compacted_sequence is None
        assert conn.total_changes == before
    assert not _notice(world, ticket_id)


def test_incomplete_cutover_prevents_polling_thread_start(prepared: tuple[_World, str]) -> None:
    from planner.runtime.worker_step_readiness_loop import WorkerStepReadinessLoop

    world, _ = prepared
    with world.connect() as conn:
        conn.execute("DROP TABLE worker_memory_notice_holds")
    event_loop = asyncio.new_event_loop()
    loop = WorkerStepReadinessLoop(
        world.db_path,
        world.clock,
        conversation_system=world.conversations,
        asyncio_loop=event_loop,
    )
    try:
        with pytest.raises(sqlite3.OperationalError):
            loop.start(1)
        assert loop._thread is None
    finally:
        event_loop.close()


def test_a_new_conversation_has_separate_permission_and_keeps_old_association(
    prepared: tuple[_World, str],
) -> None:
    from tests.unit.test_worker_memory import _add_prompt, _make_conversation

    world, ticket_id = prepared
    with closing(world.connect()) as conn:
        old_key = worker_memory.current_memory_notice_key(conn, ticket_id)
        assert old_key is not None
        original = worker_memory.consume_memory_notice_authorization(conn, old_key, now=1)
        _make_conversation(conn, "replacement-conversation", compacted=7)
        _add_prompt(
            conn,
            "replacement-conversation",
            sequence=3,
            sender_message_id="worker_step_message_replacement",
        )
        conn.execute(
            "UPDATE tickets SET conversation_id=? WHERE id=?",
            ("replacement-conversation", ticket_id),
        )
        assert (
            worker_memory.memory_notice_eligibility(conn, old_key)
            is MemoryNoticeEligibility.ineligible
        )
        new_key = worker_memory.current_memory_notice_key(conn, ticket_id)
        assert new_key is not None
        replacement = worker_memory.consume_memory_notice_authorization(conn, new_key, now=2)
        assert replacement.reservation and original.reservation
        inspection = worker_memory.inspect_memory_notices(conn, ticket_id)
        assert len(inspection.reservations) == 2
        assert original.reservation in inspection.reservations
        assert replacement.reservation.sender_message_id != original.reservation.sender_message_id


def test_event_hold_leaves_a_new_event_eligible(prepared: tuple[_World, str]) -> None:
    world, ticket_id = prepared
    with world.connect() as conn:
        key = worker_memory.current_memory_notice_key(conn, ticket_id)
        assert key is not None
        conn.execute(
            "INSERT INTO worker_memory_notice_holds VALUES (?,?,?,1,'legacy_unresolved_boundary')",
            (ticket_id, key.conversation_id, key.context_compacted_sequence),
        )
        assert worker_memory.memory_notice_eligibility(conn, key) is MemoryNoticeEligibility.held
        conn.execute(
            "INSERT INTO conversation_events VALUES (?,16,'context_compacted','{}',1)",
            (key.conversation_id,),
        )
        new_key = worker_memory.current_memory_notice_key(conn, ticket_id)
        assert new_key is not None
        assert (
            worker_memory.memory_notice_eligibility(conn, new_key)
            is MemoryNoticeEligibility.eligible
        )
