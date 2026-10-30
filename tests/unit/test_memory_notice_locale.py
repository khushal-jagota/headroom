"""Caller authorization follows current executor admission without replay on reconnect."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.unit.test_worker_memory import _add_prompt, _add_ticket, _make_conversation
from tests.unit.test_worker_step_readiness_loop import _World

from planner.conversation.contracts import (
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryUncertain,
)
from planner.core.db import connect
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.vps_inbox import InboxStore
from planner.runtime import conversation_start, worker_memory
from planner.runtime.worker_memory_contracts import MemoryNoticeEligibility
from planner.runtime.worker_step_readiness_loop import tell_a_worker_it_lost_its_memory
from planner.worker_types.configuration import configured_worker_type_registry

IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "test", 1)


@pytest.fixture
def memory_ticket(tmp_db: Connection) -> str:
    _make_conversation(tmp_db, "memory", compacted=9)
    _add_prompt(tmp_db, "memory", sequence=4, sender_message_id="worker_step_message_first")
    _add_ticket(tmp_db, "worker", claim="out", conversation_id="memory")
    return "worker"


def _store(conn: Connection) -> InboxStore:
    return InboxStore(str(conn.execute("PRAGMA database_list").fetchone()[2]))


@pytest.mark.parametrize("locale", ["vps", "macos"])
@pytest.mark.parametrize("state", ["absent", "reconciling", "ready", "lost", "inactive"])
def test_notice_scan_and_authorization_use_current_locale_admission(
    tmp_db: Connection, memory_ticket: str, locale: str, state: str
) -> None:
    tmp_db.execute("UPDATE tickets SET locale=? WHERE id=?", (locale, memory_ticket))
    store = _store(tmp_db)
    if state != "absent":
        store.runs.begin_connection(IDENTITY, 1)
    if state in {"ready", "lost", "inactive"}:
        store.runs.mark_ready(IDENTITY, 2)
    if state == "lost":
        store.runs.mark_lost(IDENTITY, 3)
    if state == "inactive":
        tmp_db.execute("UPDATE executor_connections SET active=0")
    eligible = locale == "vps" or state == "ready"
    key = worker_memory.current_memory_notice_key(tmp_db, memory_ticket)
    assert (key is not None) is eligible
    assert worker_memory.ticket_ids_holding_an_unanswered_memory_loss(tmp_db) == (
        (memory_ticket,) if eligible else ()
    )
    assert worker_memory.inspect_memory_notices(tmp_db, memory_ticket).reservations == ()


def test_mac_disconnection_between_scan_and_acquisition_sends_no_permission(
    tmp_db: Connection, memory_ticket: str
) -> None:
    tmp_db.execute("UPDATE tickets SET locale='macos' WHERE id=?", (memory_ticket,))
    store = _store(tmp_db)
    store.runs.begin_connection(IDENTITY, 1)
    store.runs.mark_ready(IDENTITY, 2)
    key = worker_memory.current_memory_notice_key(tmp_db, memory_ticket)
    assert key is not None
    store.runs.mark_lost(IDENTITY, 3)
    result = worker_memory.consume_memory_notice_authorization(tmp_db, key, now=4)
    assert result.eligibility is MemoryNoticeEligibility.ineligible
    assert result.reservation is None
    assert worker_memory.inspect_memory_notices(tmp_db, memory_ticket).reservations == ()


def test_mac_racing_polls_consume_once_and_reconnect_does_not_rearm(
    tmp_db: Connection, memory_ticket: str
) -> None:
    tmp_db.execute("UPDATE tickets SET locale='macos' WHERE id=?", (memory_ticket,))
    store = _store(tmp_db)
    store.runs.begin_connection(IDENTITY, 1)
    store.runs.mark_ready(IDENTITY, 2)
    key = worker_memory.current_memory_notice_key(tmp_db, memory_ticket)
    assert key is not None
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])

    def acquire(_: int) -> bool:
        with closing(connect(db_path)) as conn:
            return (
                worker_memory.consume_memory_notice_authorization(conn, key, now=3).reservation
                is not None
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(acquire, range(2))) == [False, True]
    original = worker_memory.inspect_memory_notices(tmp_db, memory_ticket)
    assert len(original.reservations) == 1
    assert original.evidence == ()
    store.runs.mark_lost(IDENTITY, 4)
    assert worker_memory.current_memory_notice_key(tmp_db, memory_ticket) is None
    replacement = replace(IDENTITY, connection_generation=2)
    store.runs.begin_connection(replacement, 5)
    assert worker_memory.current_memory_notice_key(tmp_db, memory_ticket) is None
    store.runs.mark_ready(replacement, 6)
    assert worker_memory.current_memory_notice_key(tmp_db, memory_ticket) == key
    assert worker_memory.memory_notice_eligibility(tmp_db, key) is MemoryNoticeEligibility.consumed
    assert worker_memory.ticket_ids_holding_an_unanswered_memory_loss(tmp_db) == ()
    assert worker_memory.inspect_memory_notices(tmp_db, memory_ticket) == original


@pytest.mark.parametrize("outcome", ["refused", "uncertain"])
def test_mac_disconnect_during_send_retains_consumption_after_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    world = _World(tmp_path)
    ticket_id = world.ready_ticket(conversation_id="memory")
    world.start_conversation("memory")
    assert world.start_step(ticket_id).started
    world.conversations.complete_running_turn("memory")
    world.record_compacted_conversation("memory", last_worker_step_sequence=4, compacted_through=9)
    with world.connect() as conn:
        conn.execute("UPDATE tickets SET locale='macos' WHERE id=?", (ticket_id,))
        store = _store(conn)
    # Commit fixture routing before the store opens its independent writer.
    store.runs.begin_connection(IDENTITY, 1)
    store.runs.mark_ready(IDENTITY, 2)
    sends: list[str] = []

    async def send(*args: object, **kwargs: object) -> conversation_start.DeliveredMessage:
        sends.append(str(kwargs["sender_message_id"]))
        assert isinstance(args[1], Connection) and not args[1].in_transaction
        with world.connect() as conn:
            assert len(worker_memory.inspect_memory_notices(conn, ticket_id).reservations) == 1
        store.runs.mark_lost(IDENTITY, 3)
        fate = (
            PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
            )
            if outcome == "refused"
            else PromptDeliveryUncertain()
        )
        return conversation_start.DeliveredMessage(conversation_id="memory", fate=fate)

    monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", send)

    def notice() -> bool:
        return asyncio.run(
            tell_a_worker_it_lost_its_memory(
                ticket_id,
                connect_database=world.connect,
                conversation_system=world.conversations,
                worker_type_registry=configured_worker_type_registry(),
                now=lambda: 3,
            )
        )

    assert notice() is (outcome == "uncertain")
    replacement = replace(IDENTITY, connection_generation=2)
    store.runs.begin_connection(replacement, 4)
    store.runs.mark_ready(replacement, 5)
    assert not notice()
    assert len(sends) == 1
    with world.connect() as conn:
        inspection = worker_memory.inspect_memory_notices(conn, ticket_id)
        assert len(inspection.reservations) == 1
        assert inspection.evidence == ()


def test_mac_loss_during_held_prompt_await_prevents_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _World(tmp_path)
    ticket_id = world.ready_ticket(conversation_id="memory")
    world.start_conversation("memory")
    assert world.start_step(ticket_id).started
    world.conversations.complete_running_turn("memory")
    world.record_compacted_conversation("memory", last_worker_step_sequence=4, compacted_through=9)
    with world.connect() as conn:
        conn.execute("UPDATE tickets SET locale='macos' WHERE id=?", (ticket_id,))
        store = _store(conn)
    # Commit fixture routing before the store opens its independent writer.
    store.runs.begin_connection(IDENTITY, 1)
    store.runs.mark_ready(IDENTITY, 2)
    held_reads: list[str] = []
    connections: list[Connection] = []
    original_connect = world.connect

    def tracked_connect() -> Connection:
        conn = original_connect()
        connections.append(conn)
        return conn

    monkeypatch.setattr(world, "connect", tracked_connect)

    async def held_prompts(conversation_id: str) -> tuple[()]:
        held_reads.append(conversation_id)
        assert not connections[-1].in_transaction
        await asyncio.sleep(0)
        store.runs.mark_lost(IDENTITY, 3)
        return ()

    async def send(*args: object, **kwargs: object) -> None:
        raise AssertionError("send after admission was lost during held-prompt read")

    monkeypatch.setattr(world.conversations, "held_prompts", held_prompts)
    monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", send)
    assert not asyncio.run(
        tell_a_worker_it_lost_its_memory(
            ticket_id,
            connect_database=world.connect,
            conversation_system=world.conversations,
            worker_type_registry=configured_worker_type_registry(),
            now=lambda: 3,
        )
    )
    assert held_reads == ["memory"]
    with world.connect() as conn:
        assert worker_memory.inspect_memory_notices(conn, ticket_id).reservations == ()
        assert (
            conn.execute(
                "SELECT worker_step_claim FROM tickets WHERE id=?", (ticket_id,)
            ).fetchone()[0]
            == "out"
        )
