"""SQLite contention must not freeze unrelated conversation reads or the fence.

An item's acceptance waiting on the database writer lock runs on a worker thread, so
the event loop keeps answering: an agent's read of another conversation (route 7) and
the routes answered from memory (the work-route fence and health). The lease and the
poll are writes themselves and wait for the lock like any write (contract §11.7, gap
c). A cancelled acceptance keeps its conversation until its blocking unit settles, and
the resend gets the one receipt, which settles the stop that waited for it.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI
from tests.support.executor_inbox import lookup_receipt
from tests.unit.test_executor_authoritative_store import CHILD, IDENTITY, envelope
from tests.unit.test_executor_authoritative_store import store as store

from planner.core.clock import RealClock
from planner.core.db import connect
from planner.execution_configuration import PROTOCOL_VERSION
from planner.executor import store as store_module
from planner.executor.protocol import (
    AcceptanceReceipt,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    JsonObject,
    LiveReadRequest,
    ReceiptDisposition,
    StopRun,
    encode_value,
)
from planner.executor.vps_inbox import InboxStore
from planner.executor.vps_transport import VpsExecutor


@pytest.mark.parametrize("blocked_unit", ["preflight", "accept"])
def test_locked_acceptance_keeps_conversation_reads_and_the_fence_live_until_cancel_settles(
    store: InboxStore, monkeypatch: pytest.MonkeyPatch, blocked_unit: str
) -> None:
    async def exercise() -> None:
        # The fixture made IDENTITY's process current; the endpoint reads it at start.
        endpoint = VpsExecutor(store.db_path, RealClock())
        endpoint.inbox.store = store
        sibling = replace(CHILD, child_id="sibling", conversation_id="other-conversation")
        store.runs.register_child(sibling, "ticket", "other-ticket", 1000)
        app = FastAPI()

        @app.get("/api/tickets/other-ticket")
        def read_sibling() -> JsonObject:
            assert store.runs.child_assignment(sibling) == ("ticket", "other-ticket")
            return {"conversation_id": sibling.conversation_id}

        endpoint.app = app
        outcome = CommandOutcome("accepted-command", CHILD, CommandDisposition.succeeded, {})
        # A stop waits for its accepted child_stopped item, the one accepted below.
        stopping = asyncio.create_task(
            endpoint.runs.stop(
                ChildCommand(outcome.command_id, CHILD, ChildOperation.stop, None, StopRun())
            )
        )
        while not endpoint.mailbox.has_waiter(outcome.command_id):
            await asyncio.sleep(0.01)
        payload = encode_value(outcome)
        assert isinstance(payload, dict)
        queued = replace(envelope(), kind=EnvelopeKind.child_stopped, payload=payload)
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
        accepting = asyncio.create_task(endpoint.inbox.accept(queued, IDENTITY))
        replay: asyncio.Task[AcceptanceReceipt] | None = None
        try:
            assert await asyncio.to_thread(waiting.wait, 2)
            assert not release.is_set()
            assert sqlite_threads and all(thread != loop_thread for thread in sqlite_threads)
            read = await asyncio.wait_for(
                endpoint.read(IDENTITY, LiveReadRequest(sibling, "/api/tickets/other-ticket", {})),
                1,
            )
            assert read == {"conversation_id": sibling.conversation_id}
            # Every work route's fence, and health, answer from memory meanwhile.
            fenced = endpoint.require_current(
                incarnation_header=IDENTITY.executor_incarnation,
                protocol_header=str(PROTOCOL_VERSION),
            )
            assert fenced.executor_incarnation == IDENTITY.executor_incarnation
            assert endpoint.health().presence == "present"
            assert not release.is_set() and not accepting.done()
            accepting.cancel()
            await asyncio.sleep(0)
            accepting.cancel()
            replay = asyncio.create_task(endpoint.inbox.accept(queued, IDENTITY))
            await asyncio.sleep(0)
            assert not accepting.done() and not replay.done()
            assert endpoint.inbox._conversation_locks[CHILD.conversation_id].locked()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(accepting, 2)
            receipt = await asyncio.wait_for(replay, 2)
            assert receipt.disposition is ReceiptDisposition.accepted
            assert lookup_receipt(store, queued.sender_id, queued.submission_id) == receipt
            assert await asyncio.wait_for(stopping, 2) == outcome
        finally:
            release.set()
            await asyncio.to_thread(holder.join, 2)
            await asyncio.gather(
                accepting, *(() if replay is None else (replay,)), return_exceptions=True
            )
            stopping.cancel()
            await asyncio.gather(stopping, return_exceptions=True)
            await endpoint.close()

    asyncio.run(exercise())
