"""Private adapters with exact fixtures and retained-core producer composition.

Synthetic SQLite files supply all storage. Actual producer cases use a temporary
runtime lease and retained guards; no server, agent home, provider or live DB runs.
These cases do not establish public consumer cutover or whole-runtime acceptance.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from starlette.types import Message

from planner.conversation.contracts import (
    SignalUnavailable,
)
from planner.conversation.publication import SignalReconciliationHandle
from planner.conversation.runtime_composition import SignalRuntimeComposition, bind_admission
from planner.core.db import ChangeSignallingConnection
from planner.core.sse import open_change_stream_count
from planner.core.transaction_guards import UncertainTransactionError
from planner.navigation import snapshot as navigation_snapshot
from planner.navigation.delivery import DeliveryRuntime
from planner.navigation.delivery_authority import DeliveryRead, DeliveryUnavailable
from planner.navigation.signal_runtime import SignalNavigationBinding


@dataclass
class _ActualBinding:
    composition: SignalRuntimeComposition
    delivery: DeliveryRuntime
    binding: SignalNavigationBinding
    path: Path

    def confirm_repaired(self) -> None:
        # This is an explicit caller action, never an automatic binding policy.
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as conn:
            conn.execute("BEGIN")
            self.composition.coordinator.confirm_repaired(conn)
            conn.rollback()


@pytest.fixture
def actual_binding(tmp_path: Path) -> Iterator[_ActualBinding]:
    path = tmp_path / "actual-signal.db"
    composition = SignalRuntimeComposition(str(path), 1000)
    composition.bootstrap()
    composition.coordinator.bind_runtime(lambda identity: None)
    conn = composition.connect()
    try:
        with bind_admission(conn, composition.initialization):
            conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
            "created_at) "
            "VALUES('actual','codex','/synthetic','full',0)"
        )
        conn.commit()
    finally:
        conn.close()
    composition.initialize()
    delivery = DeliveryRuntime(path)
    binding = SignalNavigationBinding(composition.coordinator, delivery)
    try:
        yield _ActualBinding(composition, delivery, binding, path)
    finally:
        binding.close()
        composition.close()


def test_actual_domain_callback_invalidates_threaded_snapshot_across_recovery(
    actual_binding: _ActualBinding, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.navigation import delivery

    host = actual_binding
    host.binding.attach()
    pinned = threading.Event()
    release = threading.Event()
    original = navigation_snapshot

    def blocked(conn: sqlite3.Connection) -> object:
        result = original(conn)
        pinned.set()
        assert release.wait(3), "snapshot worker was not released"
        return result

    monkeypatch.setattr(delivery, "snapshot", blocked)

    async def run() -> None:
        pending = asyncio.create_task(host.delivery.current_snapshot())
        try:
            assert await asyncio.to_thread(pinned.wait, 3)
            host.composition.coordinator.invalidate("publication_failed")
            host.confirm_repaired()
            host.binding.recover()
        finally:
            release.set()
        with pytest.raises(DeliveryUnavailable):
            await pending
        await host.delivery.current_snapshot()

    asyncio.run(run())


def test_actual_domain_callback_cancels_admitted_send_and_rejects_old_read(
    actual_binding: _ActualBinding,
) -> None:
    host = actual_binding
    host.binding.attach()

    async def run() -> None:
        read = host.delivery.admission.capture()
        entered = asyncio.Event()
        calls: list[Message] = []

        async def send(message: Message) -> None:
            calls.append(message)
            entered.set()
            await asyncio.Event().wait()

        pending = asyncio.create_task(
            host.delivery.admission.send(read, send, {"type": "http.response.body", "body": b"old"})
        )
        await entered.wait()
        host.composition.coordinator.invalidate("publication_failed")
        host.confirm_repaired()
        host.binding.recover()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(DeliveryUnavailable):
            await host.delivery.admission.send(
                read, send, {"type": "http.response.body", "body": b"late"}
            )
        assert len(calls) == 1

    asyncio.run(run())


def test_actual_domain_callback_closes_pending_stream_across_complete_recovery(
    actual_binding: _ActualBinding,
) -> None:
    host = actual_binding
    host.binding.attach()

    async def run() -> None:
        baseline = open_change_stream_count()
        value, read = await host.delivery.current_snapshot()
        assert value["mode"] == "current" and isinstance(read, DeliveryRead)
        host.delivery.ready = True
        stream = host.delivery.stream(value["cursor"], read, value["authority"])
        pending = asyncio.ensure_future(anext(stream))
        try:
            for _ in range(50):
                if open_change_stream_count() > baseline:
                    break
                await asyncio.sleep(0)
            assert open_change_stream_count() == baseline + 1
            host.composition.coordinator.invalidate("publication_failed")
            host.confirm_repaired()
            host.binding.recover()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(pending, 3)
            assert open_change_stream_count() == baseline
            await host.delivery.current_snapshot()
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            host.delivery.ready = False

    asyncio.run(run())


def test_actual_committed_uncertainty_resolves_exact_handle_before_complete_recovery(
    actual_binding: _ActualBinding, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost SQLite commit result must not repeat the durably accepted append."""
    host = actual_binding
    producer = host.composition.coordinator
    attachment = host.binding.attach()
    old_permit = attachment.capture_permit()
    old_read = host.delivery.admission.capture()
    operation_id = "source-append-once"
    conn = host.composition.connect()
    original_commit = ChangeSignallingConnection._sqlite_commit
    commit_calls: list[ChangeSignallingConnection] = []

    def lost_result(connection: ChangeSignallingConnection) -> None:
        original_commit(connection)
        commit_calls.append(connection)
        raise OSError("injected lost result after real SQLite commit")

    try:
        admission = producer.prepare_mutation(conn, None, operation_id=operation_id)
        with bind_admission(conn, admission):
            conn.execute("BEGIN IMMEDIATE")
        producer.apply_mutation(conn, admission)
        reservation = conn._transaction_guard
        assert reservation is not None
        handle = reservation.reconciliation_handle
        assert isinstance(handle, SignalReconciliationHandle)
        conn.execute("INSERT INTO conversation_events VALUES('actual',1,'message_to_owner','{}',1)")
        conn.execute("UPDATE conversations SET latest_sequence=1 WHERE conversation_id='actual'")
        producer.record_result(conn, {"sequence": 1})
        # Restore the fault seam before resolving or opening a replacement attachment.
        with monkeypatch.context() as fault:
            fault.setattr(ChangeSignallingConnection, "_sqlite_commit", lost_result)
            with pytest.raises(UncertainTransactionError, match="lost result") as caught:
                conn.commit()
        assert caught.value.reconciliation_handle is handle
        assert isinstance(caught.value.__cause__, OSError)
        assert not conn.in_transaction
    finally:
        conn.close()

    assert commit_calls == [conn]
    assert producer.signal_availability().state == "unavailable"
    with pytest.raises(DeliveryUnavailable):
        host.delivery.current_read()
    with pytest.raises(SignalUnavailable, match="resolved"):
        host.confirm_repaired()

    with sqlite3.connect(host.path.as_uri() + "?mode=ro", uri=True) as resolved:
        resolved.execute("BEGIN")
        evidence = producer.resolve_reconciliation(resolved, handle)
        assert evidence is not None and evidence[1] == {"sequence": 1}
        assert (
            evidence[0]
            == resolved.execute(
                "SELECT batch_id FROM signal_publication_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0]
        )
        producer.accept_operation_result(operation_id)
        producer.confirm_repaired(resolved)
        assert (
            resolved.execute(
                "SELECT COUNT(*) FROM conversation_events WHERE conversation_id='actual'"
            ).fetchone()[0]
            == 1
        )
        before = resolved.execute(
            "SELECT scope,runtime_epoch,sequence FROM signal_publication_meta"
        ).fetchone()
        assert tuple(before) == tuple(
            resolved.execute(
                "SELECT scope,runtime_epoch,CAST(sequence AS INTEGER) "
                "FROM navigation_signal_installation"
            ).fetchone()
        )
        assert before[2] == 1
        with pytest.raises(SignalUnavailable, match="expired"):
            producer.resolve_reconciliation(resolved, handle)
        resolved.rollback()

    # Confirming equality alone must not reopen the old attachment or delivery.
    with pytest.raises(DeliveryUnavailable):
        host.delivery.current_read()
    replacement = host.binding.recover()
    assert replacement.capture_permit().installation_id != old_permit.installation_id
    with pytest.raises(SignalUnavailable):
        attachment.validate(old_permit)
    with pytest.raises(DeliveryUnavailable):
        host.delivery.admission.validate(old_read)
    asyncio.run(host.delivery.current_snapshot())
    with sqlite3.connect(host.path.as_uri() + "?mode=ro", uri=True) as final:
        assert (
            final.execute(
                "SELECT COUNT(*) FROM conversation_events WHERE conversation_id='actual'"
            ).fetchone()[0]
            == 1
        )
        assert final.execute("SELECT sequence FROM signal_publication_meta").fetchone()[0] == 1
        assert (
            final.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0]
            == "1"
        )
        producer_value = json.loads(
            final.execute(
                "SELECT value FROM signal_publication_values WHERE conversation_id='actual'"
            ).fetchone()[0]
        )
        accepted_attention = json.loads(
            final.execute(
                "SELECT attention FROM navigation_signal_publications "
                "WHERE conversation_id='actual'"
            ).fetchone()[0]
        )
        assert accepted_attention == producer_value["attention"]
        assert accepted_attention["signal"]["message_for_owner"] is True
    assert commit_calls == [conn]
