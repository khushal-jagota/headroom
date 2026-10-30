"""SQLite contention must not freeze unrelated conversation reads or control frames."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from tests.unit.test_executor_authoritative_store import (
    CHILD,
    IDENTITY,
    envelope,
)
from tests.unit.test_executor_authoritative_store import store as store
from tests.unit.test_executor_request_deadlines import peer

from planner.core.clock import RealClock
from planner.core.db import connect
from planner.executor import store as store_module
from planner.executor.codec import encode_value
from planner.executor.contracts import (
    AcceptanceReceipt,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    JsonObject,
    LiveReadRequest,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
)
from planner.executor.journal import envelope_payload_digest
from planner.executor.store import ExecutorStore
from planner.executor.vps import ExecutorEndpoint


@pytest.mark.parametrize("blocked_unit", ["preflight", "accept"])
def test_locked_acceptance_keeps_conversation_reads_and_heartbeat_live_until_cancel_settles(
    store: ExecutorStore, monkeypatch: pytest.MonkeyPatch, blocked_unit: str
) -> None:
    async def exercise() -> None:
        endpoint = ExecutorEndpoint(store.db_path, RealClock())
        endpoint.store = store
        endpoint.identity = IDENTITY
        writer = peer()
        writer.wait_closed = AsyncMock()
        endpoint.writer = writer
        sibling = replace(CHILD, child_id="sibling", conversation_id="other-conversation")
        store.register_child(sibling, "ticket", "other-ticket", 1000)
        app = FastAPI()

        @app.get("/api/tickets/other-ticket")
        def read_sibling() -> JsonObject:
            assert store.child_assignment(sibling) == ("ticket", "other-ticket")
            return {"conversation_id": sibling.conversation_id}

        endpoint.app = app
        outcome = CommandOutcome("accepted-command", CHILD, CommandDisposition.succeeded, {})
        payload = encode_value(outcome)
        assert isinstance(payload, dict)
        queued = replace(envelope(), kind=EnvelopeKind.command_outcome, payload=payload)
        queued = replace(
            queued,
            payload_sha256=envelope_payload_digest(
                queued.payload,
                (),
                sender_id=queued.sender_id,
                conversation_id=queued.conversation_id,
                sequence=queued.sequence,
                submission_id=queued.submission_id,
                kind=queued.kind,
            ),
        )
        start_holder, held, release, waiting = (threading.Event() for _ in range(4))
        loop_thread = threading.get_ident()
        sqlite_threads: list[int] = []
        real_connect = connect

        def traced_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            connection = real_connect(*args, **kwargs)

            def trace(statement: str) -> None:
                if statement == "BEGIN IMMEDIATE" and held.is_set() and not release.is_set():
                    sqlite_threads.append(threading.get_ident())
                    waiting.set()

            connection.set_trace_callback(trace)
            return connection

        monkeypatch.setattr(store_module, "connect", traced_connect)
        original: Callable[..., AcceptanceReceipt | None] = getattr(store, blocked_unit)

        def contended(*args: Any, **kwargs: Any) -> AcceptanceReceipt | None:
            start_holder.set()
            assert held.wait(2), "write-lock holder did not start"
            return original(*args, **kwargs)

        monkeypatch.setattr(store, blocked_unit, contended)

        def hold_write_lock() -> None:
            assert start_holder.wait(2)
            # This thread owns the connection from open through rollback and close.
            connection = connect(store.db_path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                held.set()
                release.wait(5)
            finally:
                connection.rollback()
                connection.close()

        holder = threading.Thread(target=hold_write_lock)
        holder.start()
        accepting = asyncio.create_task(endpoint.accept_envelope(queued, IDENTITY))
        replay: asyncio.Task[AcceptanceReceipt] | None = None
        try:
            assert await asyncio.to_thread(waiting.wait, 2)
            assert not release.is_set()
            assert sqlite_threads and all(thread != loop_thread for thread in sqlite_threads)
            read = await asyncio.wait_for(
                endpoint._read(LiveReadRequest(sibling, "/api/tickets/other-ticket", {})), 1
            )
            assert read == {"conversation_id": sibling.conversation_id}
            heartbeat = asyncio.create_task(endpoint._measure_clock())
            while not endpoint._responses:
                await asyncio.sleep(0)
            request_id = next(iter(endpoint._responses))
            await endpoint._handle_message(
                WireMessage(
                    WireMessageKind.health,
                    request_id,
                    {"mac_received_at_ms": 1001, "mac_sent_at_ms": 1002},
                ),
                IDENTITY,
                endpoint.writer,
            )
            await asyncio.wait_for(heartbeat, 1)
            assert endpoint.clock_sample is not None
            writer.send.assert_awaited()
            assert not release.is_set() and not accepting.done()
            accepting.cancel()
            await asyncio.sleep(0)
            accepting.cancel()
            replay = asyncio.create_task(endpoint.accept_envelope(queued, IDENTITY))
            await asyncio.sleep(0)
            assert not accepting.done() and not replay.done()
            assert endpoint._conversation_locks[CHILD.conversation_id].locked()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(accepting, 2)
            receipt = await asyncio.wait_for(replay, 2)
            assert receipt.disposition is ReceiptDisposition.accepted
            assert store.lookup_receipt(queued.sender_id, queued.submission_id) == receipt
            connection = connect(store.db_path)
            try:
                assert (
                    connection.execute(
                        "SELECT count(*) FROM executor_command_outcomes WHERE command_id=?",
                        (outcome.command_id,),
                    ).fetchone()[0]
                    == 1
                )
            finally:
                connection.close()
        finally:
            release.set()
            await asyncio.to_thread(holder.join, 2)
            await asyncio.gather(
                accepting, *(() if replay is None else (replay,)), return_exceptions=True
            )
            await endpoint.close()

    asyncio.run(exercise())
