"""Automatic memory notices follow current executor admission."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.executor_presence import mac_identity, make_current
from tests.unit.test_worker_memory import _add_prompt, _add_ticket, _make_conversation
from tests.unit.test_worker_step_readiness_loop import _World

from planner.core.db import connect
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import MAC_NEEDS_UPDATE, ExecutorIdentity
from planner.executor.protocol import ExecutorRefusal
from planner.executor.vps_presence_store import record_refusal
from planner.machines.data import legacy_machine_id
from planner.runtime import worker_memory
from planner.runtime.worker_step_readiness_loop import tell_a_worker_it_lost_its_memory
from planner.worker_types.configuration import configured_worker_type_registry

IDENTITY = mac_identity()


@pytest.fixture
def memory_ticket(tmp_db: Connection) -> str:
    _make_conversation(tmp_db, "memory", compacted=9)
    _add_prompt(tmp_db, "memory", sequence=4, sender_message_id="worker_step_message_first")
    _add_ticket(tmp_db, "worker", claim="out", conversation_id="memory")
    return "worker"


def _db_path(conn: Connection) -> str:
    return str(conn.execute("PRAGMA database_list").fetchone()[2])


def _refuse_old_mac(db_path: str) -> None:
    """An old Mac's refused WebSocket, as route 10 records it."""
    server = ExecutorIdentity(PROTOCOL_VERSION, "b" * 40, SPOOL_FORMAT_VERSION, "", 0)
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        record_refusal(conn, ExecutorRefusal(MAC_NEEDS_UPDATE, None, server, 2))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("locale", "state"),
    [("vps", "absent"), ("macos", "absent"), ("macos", "away"), ("macos", "refused")],
)
def test_notice_scan_uses_current_locale_admission(
    tmp_db: Connection, memory_ticket: str, locale: str, state: str
) -> None:
    tmp_db.execute(
        "UPDATE tickets SET locale=?,machine_id=? WHERE id=?",
        (locale, legacy_machine_id(locale), memory_ticket),
    )
    tmp_db.commit()
    db_path = _db_path(tmp_db)
    if state != "absent":
        # A known compatible process, last heard from long ago: its work waits for it.
        make_current(db_path, IDENTITY, at_ms=1)
    if state == "refused":
        _refuse_old_mac(db_path)
    eligible = locale == "vps" or state == "away"
    assert worker_memory.ticket_ids_holding_an_unanswered_memory_loss(tmp_db) == (
        (memory_ticket,) if eligible else ()
    )


def test_mac_refusal_during_held_prompt_await_prevents_send(
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
        db_path = _db_path(conn)
    # Commit fixture routing before the independent writer.
    make_current(db_path, IDENTITY)
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
        # Away no longer revokes admission; a refusal does.
        _refuse_old_mac(db_path)
        return ()

    async def send(*args: object, **kwargs: object) -> None:
        raise AssertionError("send after admission was refused during held-prompt read")

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
