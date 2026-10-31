"""Automatic memory notices follow current executor admission."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.unit.test_worker_memory import _add_prompt, _add_ticket, _make_conversation
from tests.unit.test_worker_step_readiness_loop import _World

from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.vps_inbox import InboxStore
from planner.runtime import worker_memory
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


@pytest.mark.parametrize(
    ("locale", "state"), [("vps", "absent"), ("macos", "absent"), ("macos", "ready")]
)
def test_notice_scan_uses_current_locale_admission(
    tmp_db: Connection, memory_ticket: str, locale: str, state: str
) -> None:
    tmp_db.execute("UPDATE tickets SET locale=? WHERE id=?", (locale, memory_ticket))
    store = _store(tmp_db)
    if state != "absent":
        store.runs.begin_connection(IDENTITY, 1)
    if state == "ready":
        store.runs.mark_ready(IDENTITY, 2)
    eligible = locale == "vps" or state == "ready"
    assert worker_memory.ticket_ids_holding_an_unanswered_memory_loss(tmp_db) == (
        (memory_ticket,) if eligible else ()
    )


def test_mac_loss_during_held_prompt_await_prevents_send(
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
    monkeypatch.setattr(world.conversations, "send", send)
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
    assert world.ticket(ticket_id).worker_step_claim.value == "out"
