"""Synthetic SQLite transport tests, independent of agent homes and live state."""

import asyncio
import sqlite3
from collections.abc import AsyncGenerator
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message

from planner.core.sse import open_change_stream_count
from planner.navigation.api import DeadlineStreamingResponse
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.contracts import Continuity, ProjectRecord, RevisionBatch
from planner.navigation.delivery import DeliveryRuntime, read_page, read_snapshot
from planner.navigation.delivery_contracts import (
    ResetRequired,
    compact_json,
    decode_cursor,
    encode_cursor,
)
from planner.navigation.retention import prune


@pytest.fixture
def journal_db(tmp_path: Path) -> Path:
    path = tmp_path / "navigation.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE navigation_meta(singleton,scope,schema_version,epoch,revision);"
            "INSERT INTO navigation_meta VALUES(1,'workspace',2,'epoch',0);"
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
        "schema_version": 2,
        "semantics": "generic-source-facts-v2",
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
                "value": {"name": title, "priority": None},
            }
            batch["upserts"].append(record)
            conn.execute("DELETE FROM navigation_records")
            conn.execute(
                "INSERT INTO navigation_records VALUES('project','p',?)",
                (compact_json(record["value"]),),
            )
        conn.execute("INSERT INTO navigation_journal VALUES(?,?)", (revision, compact_json(batch)))
        conn.execute("UPDATE navigation_meta SET revision=?", (revision,))


def test_snapshot_handoff_complete_empty_batches_and_final_equality(journal_db: Path) -> None:
    initial = read_snapshot(journal_db)
    append(journal_db, 1, title="new")
    append(journal_db, 2)
    page = read_page(journal_db, decode_cursor(initial["cursor"]), DeliveryConfig())
    assert [b["revision"] for b in page["batches"]] == ["1", "2"]
    assert page["batches"][1]["upserts"] == []
    assert page["batches"][0]["upserts"] == read_snapshot(journal_db)["snapshot"]["records"]
    assert not page["has_more"]


def test_identity_validation_precedes_duplicate_or_future(journal_db: Path) -> None:
    for revision in ("0", "9999999999999999999999999"):
        foreign = decode_cursor(encode_cursor({**continuity(revision), "scope": "foreign"}))
        with pytest.raises(ResetRequired, match="incompatible_continuity"):
            read_page(journal_db, foreign, DeliveryConfig())
    with pytest.raises(ResetRequired, match="future_cursor"):
        read_page(journal_db, decode_cursor(encode_cursor(continuity("1"))), DeliveryConfig())


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


def test_page_count_and_serialized_byte_bounds(journal_db: Path) -> None:
    from planner.navigation.delivery_contracts import batch_frame

    for revision in range(1, 6):
        append(journal_db, revision)
    config = replace(DeliveryConfig(), frame_bytes=700, page_bytes=1100, page_batch_count=3)
    page = read_page(journal_db, decode_cursor(encode_cursor(continuity())), config)
    assert 0 < len(page["batches"]) <= 3
    assert sum(len(batch_frame(b)) for b in page["batches"]) <= 1100
    assert page["has_more"]


def test_poll_delivers_without_wake_and_shutdown_cleans_registration(journal_db: Path) -> None:
    async def run() -> None:
        baseline = open_change_stream_count()
        runtime = DeliveryRuntime(journal_db, replace(DeliveryConfig(), poll_seconds=0.01))
        runtime.ready = True  # synthetic schema intentionally has no source participants
        stream = runtime.stream(encode_cursor(continuity()))
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0.02)
        await asyncio.to_thread(append, journal_db, 1, title="quiet commit")
        frame = await asyncio.wait_for(pending, 0.5)
        assert b"event: batch" in frame
        assert b"quiet commit" in frame
        waiting = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0.01)
        await runtime.close()
        with pytest.raises(StopAsyncIteration):
            await waiting
        assert open_change_stream_count() == baseline

    asyncio.run(run())


def test_http_snapshot_and_reset_doors_preserve_ingress_identity(journal_db: Path) -> None:
    async def run() -> None:
        runtime = DeliveryRuntime(journal_db)
        app = FastAPI()
        app.include_router(runtime.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/navigation/snapshot")).status_code == 503
            runtime.ready = True
            response = await client.get("/api/navigation/snapshot")
            assert response.status_code == 200
            assert response.json()["snapshot"]["revision"] == "0"
            response = await client.get("/api/navigation/events?cursor=bad")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert b'"reason":"invalid_cursor"' in response.content
            assert b'"snapshot_url":"/api/navigation/snapshot"' in response.content

    asyncio.run(run())


def test_send_deadline_releases_iterator_and_other_client_continues(journal_db: Path) -> None:
    async def run() -> None:
        runtime = DeliveryRuntime(journal_db)
        runtime.ready = True
        append(journal_db, 1)
        baseline = open_change_stream_count()
        response = DeadlineStreamingResponse(
            runtime.stream(encode_cursor(continuity())), send_timeout=0.01
        )

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body":
                await asyncio.sleep(1)

        await response.stream_response(send)
        assert open_change_stream_count() == baseline
        other = runtime.stream(encode_cursor(continuity()))
        assert b'"revision":"1"' in await anext(other)
        await cast(AsyncGenerator[bytes, None], other).aclose()
        await runtime.close()

    asyncio.run(run())


def test_pruning_between_pages_resets_without_skipping(journal_db: Path) -> None:
    for revision in range(1, 5):
        append(journal_db, revision)
    config = replace(DeliveryConfig(), page_batch_count=1, retained_batch_count=1)
    first = read_page(journal_db, decode_cursor(encode_cursor(continuity())), config)
    assert first["batches"][0]["revision"] == "1"
    with sqlite3.connect(journal_db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        prune(conn, config)
    with pytest.raises(ResetRequired, match="expired_history"):
        read_page(journal_db, decode_cursor(encode_cursor(continuity("1"))), config)


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


def test_epoch_reset_rejects_previously_complete_cursor_before_duplicate(journal_db: Path) -> None:
    old = decode_cursor(encode_cursor(continuity()))
    with sqlite3.connect(journal_db) as conn:
        conn.execute("UPDATE navigation_meta SET epoch='fresh'")
    with pytest.raises(ResetRequired, match="incompatible_continuity"):
        read_page(journal_db, old, DeliveryConfig())


def test_delete_is_one_complete_revision_frame(journal_db: Path) -> None:
    from planner.navigation.delivery_contracts import batch_frame

    append(journal_db, 1)
    with sqlite3.connect(journal_db) as conn:
        body = {
            **continuity("1"),
            "base_revision": "0",
            "upserts": [],
            "deletes": [{"kind": "project", "id": "gone"}],
        }
        conn.execute("UPDATE navigation_journal SET body=?", (compact_json(body),))
    page = read_page(journal_db, decode_cursor(encode_cursor(continuity())), DeliveryConfig())
    frame = batch_frame(page["batches"][0])
    assert frame.count(b"event: batch") == 1
    assert frame.endswith(b"\n\n")
    assert b'"deletes":[{"id":"gone","kind":"project"}]' in frame


def test_send_error_releases_stream_registration(journal_db: Path) -> None:
    async def run() -> None:
        runtime = DeliveryRuntime(journal_db)
        runtime.ready = True
        append(journal_db, 1)
        baseline = open_change_stream_count()
        response = DeadlineStreamingResponse(
            runtime.stream(encode_cursor(continuity())), send_timeout=0.1
        )

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body":
                raise OSError("disconnected")

        with pytest.raises(OSError, match="disconnected"):
            await response.stream_response(send)
        assert open_change_stream_count() == baseline
        await runtime.close()

    asyncio.run(run())


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
