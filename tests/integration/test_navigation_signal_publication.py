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
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from starlette.types import Message
from tests.integration.test_navigation_transactions import add_ticket, database, values

from planner.conversation.contracts import (
    ConversationAttention,
    ConversationSignalPublicationV1,
    SignalAssociationEffect,
    SignalBatch,
    SignalCursor,
    SignalDeletion,
    SignalReadPermit,
    SignalSnapshot,
    SignalUnavailable,
)
from planner.conversation.publication import SignalReconciliationHandle
from planner.conversation.runtime_composition import SignalRuntimeComposition, bind_admission
from planner.core.db import ChangeSignallingConnection
from planner.core.sse import open_change_stream_count
from planner.core.transaction_guards import UncertainTransactionError
from planner.navigation import snapshot as navigation_snapshot
from planner.navigation.delivery import DeliveryRuntime
from planner.navigation.delivery_authority import DeliveryUnavailable
from planner.navigation.signal_publication import (
    SignalInstallationError,
    delivery_authority,
    install,
    participate,
    validate_installation,
)
from planner.navigation.signal_runtime import SignalNavigationBinding


def publication(identity: str, revision: int = 0) -> ConversationSignalPublicationV1:
    return ConversationSignalPublicationV1(
        identity,
        "runtime",
        revision,
        ConversationAttention(
            signal={"working": True, "message_for_owner": False},
            awaiting_answer=False,
            last_turn_failed=True,
        ),
    )


def complete() -> SignalSnapshot:
    return SignalSnapshot(
        "installation",
        SignalCursor("producer-scope", "runtime", 4),
        (publication("current"), publication("historical", 12)),
    )


def batch(sequence: int = 5) -> SignalBatch:
    return SignalBatch(
        SignalCursor("producer-scope", "runtime", sequence - 1),
        SignalCursor("producer-scope", "runtime", sequence),
        f"batch-{sequence}",
        (f"operation-{sequence}",),
        f"fingerprint-{sequence}",
        (),
        (),
        (),
    )


@pytest.fixture
def signal_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = database(tmp_path)
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES(?,'codex','/synthetic','full',0)",
        [("current",), ("historical",)],
    )
    add_ticket(conn, "ticket")
    conn.execute("UPDATE tickets SET conversation_id='current' WHERE id='ticket'")
    conn.execute("INSERT INTO ticket_conversations VALUES('historical','ticket')")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def dump(conn: sqlite3.Connection) -> str:
    return "\n".join(conn.iterdump())


def install_fixture(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")
    install(conn, complete())
    conn.commit()


def test_complete_install_and_private_schema_rollback_preserve_public_shape(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    before = dump(conn)
    old_epoch = navigation_snapshot(conn)["epoch"]
    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET conversation_id='historical' WHERE id='ticket'")
    install(conn, complete())
    actual = values(conn)
    addresses = [value for (kind, _), value in actual.items() if kind == "address"]
    assert any(value["conversation_id"] == "historical" and value["current"] for value in addresses)
    assert navigation_snapshot(conn)["epoch"] != old_epoch
    assert navigation_snapshot(conn)["schema_version"] == 3
    assert "attention" not in actual["conversation", "current"]
    assert conn.execute("SELECT COUNT(*) FROM navigation_signal_publications").fetchone()[0] == 2
    conn.rollback()
    assert dump(conn) == before
    install_fixture(conn)
    assert navigation_snapshot(conn)["epoch"] != old_epoch


def test_complete_install_rejects_partial_duplicate_and_invalid_fences_atomically(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    baseline = dump(conn)
    original = complete()
    invalid = (
        replace(original, values=original.values[:1]),
        replace(original, values=(*original.values, original.values[0])),
        replace(original, values=(publication("current", -1), publication("historical"))),
        replace(
            original,
            values=(
                replace(publication("current"), runtime_epoch="old"),
                publication("historical"),
            ),
        ),
        replace(original, cursor=replace(original.cursor, sequence=-1)),
    )
    for candidate in invalid:
        conn.execute("BEGIN")
        with pytest.raises(SignalInstallationError):
            install(conn, candidate)
        conn.rollback()
        assert dump(conn) == baseline


def test_cursor_accepts_exact_duplicate_and_rejects_conflicts_gaps_and_foreign_epochs(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    install_fixture(conn)
    accepted = replace(batch(), replacements=(publication("current", 100),))
    conn.execute("BEGIN")
    participate(conn, accepted)
    conn.commit()
    baseline = dump(conn)
    conn.execute("BEGIN")
    participate(conn, accepted)
    conn.commit()
    assert dump(conn) == baseline
    invalid = (
        replace(accepted, fingerprint="different"),
        replace(accepted, replacements=(publication("current", 101),)),
        batch(7),
        batch(4),
        replace(
            batch(6),
            base_cursor=SignalCursor("foreign", "runtime", 5),
            cursor=SignalCursor("foreign", "runtime", 6),
        ),
        replace(
            batch(6),
            base_cursor=SignalCursor("producer-scope", "old", 5),
            cursor=SignalCursor("producer-scope", "old", 6),
        ),
        replace(batch(6), replacements=(publication("current", 101), publication("current", 102))),
    )
    for candidate in invalid:
        conn.execute("BEGIN")
        with pytest.raises(SignalInstallationError):
            participate(conn, candidate)
        conn.rollback()
        assert dump(conn) == baseline


def test_association_and_order_only_batches_advance_cursor_without_signal_wire_change(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    install_fixture(conn)
    before = navigation_snapshot(conn)
    conn.execute("BEGIN")
    participate(conn, batch())
    conn.commit()
    assert navigation_snapshot(conn) == before
    assert conn.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0] == "5"
    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET conversation_id='historical' WHERE id='ticket'")
    participate(
        conn,
        replace(
            batch(6),
            associations=(
                SignalAssociationEffect(
                    "ticket",
                    "ticket",
                    "current",
                    "current",
                    "historical",
                ),
            ),
        ),
    )
    conn.commit()
    addresses = [value for (kind, _), value in values(conn).items() if kind == "address"]
    assert any(value["conversation_id"] == "historical" and value["current"] for value in addresses)
    assert conn.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0] == "6"


def test_deletion_tombstone_rejects_obsolete_delete_and_same_epoch_resurrection(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    install_fixture(conn)
    conn.execute("BEGIN")
    conn.execute("DELETE FROM ticket_conversations WHERE conversation_id='historical'")
    conn.execute("DELETE FROM conversations WHERE conversation_id='historical'")
    participate(conn, replace(batch(), deletions=(SignalDeletion("historical", "runtime", 20),)))
    conn.commit()
    assert tuple(
        conn.execute(
            "SELECT conversation_revision,attention FROM navigation_signal_publications "
            "WHERE conversation_id='historical'"
        ).fetchone()
    ) == ("20", None)
    before = dump(conn)
    invalid = (
        replace(batch(6), replacements=(publication("historical", 19),)),
        replace(batch(6), deletions=(SignalDeletion("historical", "runtime", 20),)),
        replace(batch(6), deletions=(SignalDeletion("current", "runtime", -1),)),
    )
    for candidate in invalid:
        conn.execute("BEGIN")
        with pytest.raises(SignalInstallationError):
            participate(conn, candidate)
        conn.rollback()
        assert dump(conn) == before


def test_batch_rollback_restores_source_publication_cursor_and_large_integer_fences(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    huge = 2**70
    conn.execute("BEGIN")
    install(conn, replace(complete(), cursor=SignalCursor("producer-scope", "runtime", huge)))
    conn.commit()
    before = dump(conn)
    conn.execute("BEGIN")
    conn.execute("UPDATE conversations SET latest_sequence=5 WHERE conversation_id='current'")
    participate(conn, replace(batch(huge + 1), replacements=(publication("current", huge + 10),)))
    stored = conn.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0]
    assert stored == str(huge + 1)
    conn.rollback()
    assert dump(conn) == before


def test_exact_attachment_binding_maps_unavailable_and_rejects_old_sql_installation(
    signal_db: sqlite3.Connection,
) -> None:
    conn = signal_db
    install_fixture(conn)
    permit = SignalReadPermit("producer-scope", "runtime", "installation", 1)

    class Attachment:
        available = True

        def capture_permit(self) -> SignalReadPermit:
            self.validate(permit)
            return permit

        def validate(self, value: SignalReadPermit, /) -> None:
            if not self.available or value != permit:
                raise SignalUnavailable("injected attachment unavailable")

        def close(self) -> None:
            self.available = False

    attachment = Attachment()
    callbacks = delivery_authority(attachment)
    assert callbacks.capture_permit() == permit
    callbacks.validate(permit)
    conn.execute("BEGIN")
    callbacks.validate_snapshot(conn, permit)
    install(conn, replace(complete(), installation_id="replacement"))
    with pytest.raises(DeliveryUnavailable, match="installation changed"):
        validate_installation(conn, permit)
    conn.rollback()
    attachment.close()
    with pytest.raises(DeliveryUnavailable, match="injected attachment"):
        callbacks.capture_permit()
    with pytest.raises(DeliveryUnavailable, match="injected attachment"):
        callbacks.validate(permit)
    with pytest.raises(DeliveryUnavailable, match="exact permit"):
        callbacks.validate(object())


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


def test_actual_producer_binding_installs_and_accepts_committed_source_boundary(
    actual_binding: _ActualBinding,
) -> None:
    host = actual_binding
    with pytest.raises(DeliveryUnavailable, match="no active attachment"):
        host.delivery.admission.capture()
    attachment = host.binding.attach()
    host.composition.coordinator.admit_runtime()
    original = attachment.capture_permit()
    conn = host.composition.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO conversation_events VALUES('actual',1,'message_to_owner','{}',1)")
        conn.execute("UPDATE conversations SET latest_sequence=1 WHERE conversation_id='actual'")
        conn.commit()
        assert (
            conn.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0] == "1"
        )
        private = json.loads(
            conn.execute(
                "SELECT attention FROM navigation_signal_publications "
                "WHERE conversation_id='actual'"
            ).fetchone()[0]
        )
        assert private["signal"]["message_for_owner"] is True
        # The private assembly does not widen the public record contract.
        assert "attention" not in values(conn)["conversation", "actual"]
    finally:
        conn.close()
    assert attachment.capture_permit() == original
    value, read = asyncio.run(host.delivery.current_snapshot())
    assert read is not None
    assert any(record["id"] == "actual" for record in value["snapshot"]["records"])


def test_actual_producer_same_thread_detach_rejection_preserves_binding(
    actual_binding: _ActualBinding,
) -> None:
    host = actual_binding
    attachment = host.binding.attach()
    read = host.delivery.admission.capture()
    conn = host.composition.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(SignalUnavailable, match="candidate transaction"):
            host.binding.close()
        conn.rollback()
        host.delivery.admission.validate(read)
        attachment.validate(attachment.capture_permit())
        # A retained participant still publishes after the rejected detach.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE conversations SET latest_sequence=2 WHERE conversation_id='actual'")
        conn.commit()
        assert (
            conn.execute("SELECT sequence FROM navigation_signal_installation").fetchone()[0] == "1"
        )
    finally:
        conn.close()
    host.binding.close()
    assert host.composition.ownership is not None
    assert not host.composition.ownership.closed
    with pytest.raises(DeliveryUnavailable, match="no active attachment"):
        host.delivery.admission.capture()


def test_actual_binding_recovery_does_not_infer_domain_repair(
    actual_binding: _ActualBinding,
) -> None:
    host = actual_binding
    old = host.binding.attach()
    read = host.delivery.admission.capture()
    old_installation = old.capture_permit().installation_id
    host.composition.coordinator.invalidate("publication_failed")
    with pytest.raises(DeliveryUnavailable):
        host.delivery.admission.validate(read)
    with pytest.raises(SignalUnavailable, match="exact outcome repair"):
        host.binding.recover()
    with pytest.raises(DeliveryUnavailable, match="no active attachment"):
        host.delivery.admission.capture()
    host.confirm_repaired()
    replacement = host.binding.recover()
    assert replacement.capture_permit().installation_id != old_installation
    with pytest.raises(DeliveryUnavailable):
        host.delivery.admission.validate(read)
    asyncio.run(host.delivery.current_snapshot())


def test_actual_postcommit_install_failure_keeps_delivery_unavailable(
    actual_binding: _ActualBinding, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = actual_binding
    producer = host.composition.coordinator
    original = producer._register_callbacks

    def reject(*args: object) -> None:
        raise RuntimeError("injected postcommit registration failure")

    monkeypatch.setattr(producer, "_register_callbacks", reject)
    with pytest.raises(RuntimeError, match="postcommit registration"):
        host.binding.attach()
    assert producer.signal_availability().state == "unavailable"
    with pytest.raises(DeliveryUnavailable, match="no active attachment"):
        host.delivery.admission.capture()
    conn = host.composition.connect()
    try:
        # Installation committed before callback failure; it still grants no delivery.
        assert conn.execute("SELECT installation_id FROM navigation_signal_installation").fetchone()
    finally:
        conn.close()
    monkeypatch.setattr(producer, "_register_callbacks", original)
    host.binding.recover()
    asyncio.run(host.delivery.current_snapshot())


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
        host.delivery.ready = True
        stream = host.delivery.stream(value["cursor"], read)
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
        asyncio.run(host.delivery.current_snapshot())
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
        asyncio.run(host.delivery.current_snapshot())
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
