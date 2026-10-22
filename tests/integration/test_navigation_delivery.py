"""Synthetic SQLite transport tests, independent of agent homes and live state."""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from tests.integration.test_navigation_signal_publication import (
    _ActualBinding,
)
from tests.integration.test_navigation_signal_publication import (
    actual_binding as actual_binding,
)

from planner.conversation.contracts import (
    SignalAttachment,
    SignalAvailability,
    SignalInstaller,
    SignalInvalidation,
    SignalKnowledge,
    SignalParticipant,
    SignalReadPermit,
)
from planner.core.sse import open_change_stream_count
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.contracts import Continuity, ProjectRecord, RevisionBatch
from planner.navigation.delivery import DeliveryRuntime, read_page, read_snapshot
from planner.navigation.delivery_authority import (
    DeliveryAuthority,
    DeliveryUnavailable,
)
from planner.navigation.delivery_contracts import (
    ResetRequired,
    compact_json,
    decode_cursor,
    encode_cursor,
)
from planner.navigation.retention import prune


class InjectedKnowledgeSource:
    """Empty-domain transport fixture; supplies no real conversation fact policy."""

    def __init__(self) -> None:
        self.availability = SignalAvailability(1, "current", None)

    def signal_availability(self) -> SignalAvailability:
        return self.availability

    def read_durable_signal_knowledge(
        self,
        connection: sqlite3.Connection,
        conversation_ids: tuple[str, ...],
        expected_generation: int,
        /,
    ) -> tuple[SignalKnowledge, ...]:
        assert conversation_ids == ()
        assert expected_generation == self.availability.generation
        return ()

    def attach_signal_publication(
        self,
        *,
        install: SignalInstaller,
        participate: SignalParticipant,
        unavailable: Callable[[SignalInvalidation], None],
    ) -> SignalAttachment:
        raise AssertionError("this fixture never installs a producer")


def injected_runtime(path: Path, config: DeliveryConfig | None = None) -> DeliveryRuntime:
    runtime = DeliveryRuntime(path, config)
    runtime.bind_signal_source(InjectedKnowledgeSource())
    runtime.admission.configure(InjectedDeliveryAuthority().callbacks())
    return runtime


@pytest.fixture
def journal_db(tmp_path: Path) -> Path:
    path = tmp_path / "navigation.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE navigation_meta(singleton,scope,schema_version,epoch,revision);"
            "INSERT INTO navigation_meta VALUES(1,'workspace',8,'epoch',0);"
            "CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY);"
            "CREATE TABLE navigation_records(kind,id,value);"
            "CREATE TABLE navigation_dirty(kind,id);"
            "CREATE TABLE navigation_journal(revision INTEGER PRIMARY KEY,body TEXT);"
            "CREATE TABLE navigation_delivery_retention(singleton INTEGER PRIMARY KEY,"
            "epoch TEXT,floor_revision INTEGER);"
        )
    return path


def continuity(revision: str = "0") -> Continuity:
    return {
        "scope": "workspace",
        "schema_version": 8,
        "semantics": "attention-knowledge-v8-manager-updated-at",
        "epoch": "epoch",
        "revision": revision,
    }


def append(path: Path, revision: int, *, title: str | None = None) -> None:
    with sqlite3.connect(path) as conn:
        batch: RevisionBatch = {
            **continuity(str(revision)),
            "base_revision": str(revision - 1),
            "upserts": [],
            "deletes": [],
        }
        if title is not None:
            record: ProjectRecord = {
                "kind": "project",
                "id": "p",
                "value": {"name": title},
            }
            batch["upserts"].append(record)
            conn.execute("DELETE FROM navigation_records")
            conn.execute(
                "INSERT INTO navigation_records VALUES('project','p',?)",
                (compact_json(record["value"]),),
            )
        conn.execute("INSERT INTO navigation_journal VALUES(?,?)", (revision, compact_json(batch)))
        conn.execute("UPDATE navigation_meta SET revision=?", (revision,))


def test_retention_prefix_expiry_and_gap_are_explicit(journal_db: Path) -> None:
    for revision in range(1, 5):
        append(journal_db, revision)
    config = replace(DeliveryConfig(), retained_batch_count=2)
    with sqlite3.connect(journal_db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert prune(conn, config) == 2
    with pytest.raises(ResetRequired, match="expired_history"):
        read_page(journal_db, decode_cursor(encode_cursor(continuity())), config)
    page = read_page(journal_db, decode_cursor(encode_cursor(continuity("2"))), config)
    assert [b["revision"] for b in page["batches"]] == ["3", "4"]
    with sqlite3.connect(journal_db) as conn:
        conn.execute("DELETE FROM navigation_journal WHERE revision=3")
    with pytest.raises(ResetRequired, match="journal_gap"):
        read_page(journal_db, decode_cursor(encode_cursor(continuity("2"))), config)


def test_oversize_batch_recovers_via_unrestricted_snapshot(journal_db: Path) -> None:
    append(journal_db, 1, title="x" * 4000)
    config = replace(DeliveryConfig(), frame_bytes=600, page_bytes=800)
    with pytest.raises(ResetRequired, match="subscriber_overflow"):
        read_page(journal_db, decode_cursor(encode_cursor(continuity())), config)
    recovery = read_snapshot(journal_db)
    record = recovery["snapshot"]["records"][0]
    assert record["kind"] == "project"
    assert len(record["value"]["name"]) == 4000
    assert read_page(journal_db, decode_cursor(recovery["cursor"]), config)["batches"] == []


def test_poll_delivers_without_wake_and_shutdown_cleans_registration(journal_db: Path) -> None:
    async def run() -> None:
        baseline = open_change_stream_count()
        runtime = injected_runtime(journal_db, replace(DeliveryConfig(), poll_seconds=0.01))
        runtime.ready = True  # synthetic schema intentionally has no source participants
        stream = runtime.stream(encode_cursor(continuity()))
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0.02)
        await asyncio.to_thread(append, journal_db, 1, title="quiet commit")
        frame = await asyncio.wait_for(pending, 0.5)
        assert b"event: batch" in frame
        assert b"quiet commit" in frame
        envelope = json.loads(frame.split(b"data: ", 1)[1].strip())
        assert envelope["wire_version"] == 2
        assert envelope["authority"] == runtime.current_read()[1]
        waiting = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0.01)
        await runtime.close()
        with pytest.raises(StopAsyncIteration):
            await waiting
        assert open_change_stream_count() == baseline

    asyncio.run(run())


def test_retention_byte_limit_can_prune_every_batch_and_snapshot_recovers(journal_db: Path) -> None:
    append(journal_db, 1, title="large" * 2000)
    config = replace(DeliveryConfig(), retained_bytes=100)
    with sqlite3.connect(journal_db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert prune(conn, config) == 1
        assert conn.execute(
            "SELECT floor_revision FROM navigation_delivery_retention"
        ).fetchone() == (1,)
    with pytest.raises(ResetRequired, match="expired_history"):
        read_page(journal_db, decode_cursor(encode_cursor(continuity())), config)
    recovery = read_snapshot(journal_db)
    assert read_page(journal_db, decode_cursor(recovery["cursor"]), config)["batches"] == []


def test_concurrent_snapshot_and_writer_keep_one_consistent_revision(
    journal_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from planner.navigation import delivery

    with sqlite3.connect(journal_db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    pinned = threading.Event()
    release = threading.Event()
    original = delivery._read_connection

    def traced_connection(path: Path) -> sqlite3.Connection:
        conn = original(path)

        def trace(statement: str) -> None:
            if statement.startswith("SELECT * FROM navigation_records"):
                pinned.set()
                assert release.wait(3), "writer did not release pinned snapshot"

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(delivery, "_read_connection", traced_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(delivery.read_snapshot, journal_db)
        try:
            assert pinned.wait(3), "snapshot did not pin its continuity transaction"
            # This commit finishes while the reader is paused in its existing read txn.
            append(journal_db, 1, title="committed during snapshot")
        finally:
            release.set()
        initial = reading.result(timeout=3)
    assert initial["snapshot"]["revision"] == "0"
    assert initial["snapshot"]["records"] == []
    page = read_page(journal_db, decode_cursor(initial["cursor"]), DeliveryConfig())
    assert [batch["revision"] for batch in page["batches"]] == ["1"]
    final = read_snapshot(journal_db)["snapshot"]
    assert page["batches"][0]["upserts"] == final["records"]


class InjectedDeliveryAuthority:
    """Opaque fixture permits exercise delivery, not Signal policy or contracts."""

    def __init__(self) -> None:
        self.permit = SignalReadPermit("source", "runtime", "installed", 1)
        self.available = True
        self.epoch = "epoch"

    def capture_permit(self) -> object:
        self.validate(self.permit)
        return self.permit

    def validate(self, permit: object) -> None:
        if not self.available or permit is not self.permit:
            raise DeliveryUnavailable("injected authority is unavailable")

    def validate_snapshot(self, conn: sqlite3.Connection, permit: object) -> None:
        if conn.execute("SELECT epoch FROM navigation_meta").fetchone()[0] != self.epoch:
            raise DeliveryUnavailable("injected installation does not match the read")

    def callbacks(self) -> DeliveryAuthority:
        return DeliveryAuthority(self.capture_permit, self.validate, self.validate_snapshot)


def test_actual_current_and_degraded_snapshots_keep_canonical_durable_facts(
    actual_binding: _ActualBinding,
) -> None:
    host = actual_binding
    host.binding.attach()
    conn = host.composition.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO conversation_events VALUES('actual',1,'message_to_owner','{}',1)")
        conn.execute("UPDATE conversations SET latest_sequence=1 WHERE conversation_id='actual'")
        conn.commit()
    finally:
        conn.close()
    current, _ = asyncio.run(host.delivery.current_snapshot())
    assert current["mode"] == "current"
    assert current["wire_version"] == 2
    assert current["authority"]["permit"]["scope"] != current["snapshot"]["scope"]
    assert (
        current["authority"]["permit"]["generation"]
        == current["authority"]["availability"]["generation"]
    )
    host.composition.coordinator.invalidate("publication_failed")
    degraded, _ = asyncio.run(host.delivery.current_snapshot({"not": "a proof request"}))
    assert degraded["mode"] == "degraded"
    assert degraded["cursor"] is None and degraded["proof"] is None
    assert degraded["authority"]["permit"] is None
    record = next(
        value for value in degraded["snapshot"]["records"] if value["kind"] == "conversation"
    )
    assert record["kind"] == "conversation"
    assert record["value"]["attention"] == {
        "current": None,
        "durable": {"message_for_owner": True, "last_turn_failed": False},
    }
    assert (
        degraded["authority"]["delivery_session_id"] == current["authority"]["delivery_session_id"]
    )
    host.confirm_repaired()
    host.binding.recover()
    recovered, _ = asyncio.run(host.delivery.current_snapshot())
    assert recovered["mode"] == "current"
    assert recovered["authority"]["permit"] != current["authority"]["permit"]
    assert recovered["snapshot"]["epoch"] != current["snapshot"]["epoch"]
