"""Durable, bounded navigation replay. Wakes accelerate polling but never replace it."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal, cast

from planner.core import change_signal
from planner.core.sse import register_open_stream_closer
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.contracts import (
    SUPPORTED_SEMANTICS,
    Continuity,
    RevisionBatch,
    parse_revision,
)
from planner.navigation.data import snapshot
from planner.navigation.delivery_contracts import (
    Cursor,
    ReplayPage,
    ResetRequired,
    SnapshotResponse,
    batch_frame,
    decode_cursor,
    encode_cursor,
    reset_frame,
)
from planner.navigation.retention import prune


def _read_connection(path: Path) -> sqlite3.Connection:
    # No schema opens, migrations, journal-mode changes, or live writable fallback.
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    conn.execute("PRAGMA query_only=ON")
    return conn


def read_snapshot(path: Path, fence: object = None) -> SnapshotResponse:
    from planner.navigation.proof import certify

    with contextlib.closing(_read_connection(path)) as conn:
        conn.execute("BEGIN")
        try:
            value = snapshot(conn)
            proof = certify(conn, fence) if fence is not None else None
            return {
                "wire_version": 1,
                "cursor": encode_cursor(value),
                "snapshot": value,
                "proof": proof,
            }
        finally:
            conn.rollback()


def read_page(path: Path, cursor: Cursor, config: DeliveryConfig) -> ReplayPage:
    with contextlib.closing(_read_connection(path)) as conn:
        conn.execute("BEGIN")
        try:
            scope, schema, epoch, current = conn.execute(
                "SELECT scope,schema_version,epoch,revision FROM navigation_meta"
            ).fetchone()
            continuity: Continuity = {
                "scope": scope,
                "schema_version": schema,
                "semantics": cast(Literal["generic-source-facts-v2"], SUPPORTED_SEMANTICS),
                "epoch": epoch,
                "revision": str(current),
            }
            # Validate EVERY continuity field before duplicate/future/expired decisions.
            if (
                any(
                    dict(cursor)[key] != dict(continuity)[key]
                    for key in ("scope", "schema_version", "semantics", "epoch")
                )
                or schema != 2
            ):
                raise ResetRequired("incompatible_continuity")
            after = parse_revision(cursor["revision"])
            if after > current:
                raise ResetRequired("future_cursor")
            floor_row = conn.execute(
                "SELECT epoch,floor_revision FROM navigation_delivery_retention WHERE singleton=1"
            ).fetchone()
            floor = int(floor_row[1]) if floor_row and floor_row[0] == epoch else 0
            if after < floor:
                raise ResetRequired("expired_history")
            if after == current:
                return {"batches": [], "has_more": False}
            # Inspect size before fetching bodies, including individually oversized batches.
            entries = conn.execute(
                "SELECT revision,length(CAST(body AS BLOB)) FROM navigation_journal "
                "WHERE revision>? ORDER BY revision LIMIT ?",
                (after, config.page_batch_count),
            ).fetchall()
            batches: list[RevisionBatch] = []
            used = 0
            expected = after + 1
            for revision, body_bytes in entries:
                if revision != expected:
                    raise ResetRequired("journal_gap")
                if body_bytes > config.frame_bytes:
                    if batches:
                        break
                    raise ResetRequired("subscriber_overflow")
                row = conn.execute(
                    "SELECT body FROM navigation_journal WHERE revision=?", (revision,)
                ).fetchone()
                batch = cast(RevisionBatch, json.loads(row[0]))
                if any(
                    dict(batch)[key] != dict(continuity)[key]
                    for key in ("scope", "schema_version", "semantics", "epoch")
                ) or (batch["base_revision"], batch["revision"]) != (
                    str(revision - 1),
                    str(revision),
                ):
                    raise ResetRequired("journal_gap")
                frame_bytes = len(batch_frame(batch))
                if frame_bytes > config.frame_bytes:
                    if batches:
                        break
                    raise ResetRequired("subscriber_overflow")
                if used + frame_bytes > config.page_bytes:
                    break
                batches.append(batch)
                used += frame_bytes
                expected += 1
            if not batches:
                raise ResetRequired("journal_gap")
            return {"batches": batches, "has_more": expected <= current}
        finally:
            conn.rollback()


class DeliveryRuntime:
    def __init__(self, path: str | Path, config: DeliveryConfig | None = None) -> None:
        from planner.navigation.api import delivery_router

        self.path = Path(path)
        self.config = config or DeliveryConfig()
        self.ready = False
        self._closing = asyncio.Event()
        self._retention_task: asyncio.Task[None] | None = None
        self._stream_closers: dict[asyncio.Event, asyncio.Event] = {}
        self.router = delivery_router(self)

    async def start(self) -> None:
        if self.ready:
            raise RuntimeError("navigation delivery already started")
        # Owner startup has already reconciled and committed a fresh epoch.
        await asyncio.to_thread(self._prune)
        self._closing.clear()
        self.ready = True
        self._retention_task = asyncio.create_task(self._retain(), name="navigation-retention")

    def _prune(self) -> None:
        from planner.core.db import commit_without_change_signal, connect

        with contextlib.closing(connect(str(self.path))) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prune(conn, self.config)
                commit_without_change_signal(conn)
            except BaseException:
                conn.rollback()
                raise

    async def _retain(self) -> None:
        while not self._closing.is_set():
            try:
                await asyncio.wait_for(self._closing.wait(), self.config.retention_seconds)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self._prune)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "navigation retention failed; retry next interval"
                    )

    async def close(self) -> None:
        self.ready = False
        self._closing.set()
        for closer in tuple(self._stream_closers):
            closer.set()
        drained = tuple(self._stream_closers.values())
        if drained:
            await asyncio.gather(*(done.wait() for done in drained))
        if self._retention_task is not None:
            await self._retention_task
            self._retention_task = None

    async def stream(self, encoded_cursor: str) -> AsyncIterator[bytes]:
        try:
            cursor = decode_cursor(encoded_cursor, self.config.cursor_bytes)
        except ResetRequired as exc:
            yield reset_frame(exc.reason)
            return
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        closed = asyncio.Event()
        drained = asyncio.Event()
        self._stream_closers[closed] = drained

        def hint() -> None:
            loop.call_soon_threadsafe(wake.set)

        def close() -> None:
            loop.call_soon_threadsafe(closed.set)

        unsubscribe = change_signal.subscribe(hint)
        forget = register_open_stream_closer(close)
        last_frame = time.monotonic()
        try:
            while self.ready and not closed.is_set():
                wake.clear()
                try:
                    page = await asyncio.to_thread(read_page, self.path, cursor, self.config)
                except ResetRequired as exc:
                    yield reset_frame(exc.reason)
                    return
                if closed.is_set() or not self.ready:
                    return
                for batch in page["batches"]:
                    # Each yield owns one complete revision; there is no per-record progress.
                    yield batch_frame(batch)
                    cursor = decode_cursor(encode_cursor(cast(Continuity, batch)))
                    last_frame = time.monotonic()
                    if closed.is_set() or not self.ready:
                        return
                if page["has_more"]:
                    continue
                if time.monotonic() - last_frame >= self.config.heartbeat_seconds:
                    yield b": keep-alive\n\n"
                    last_frame = time.monotonic()
                waiter = asyncio.create_task(wake.wait())
                closer = asyncio.create_task(closed.wait())
                try:
                    await asyncio.wait(
                        (waiter, closer),
                        timeout=self.config.poll_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    waiter.cancel()
                    closer.cancel()
                    await asyncio.gather(waiter, closer, return_exceptions=True)
        finally:
            self._stream_closers.pop(closed, None)
            forget()
            unsubscribe()
            drained.set()


def create_delivery_runtime(
    path: str | Path,
    config: DeliveryConfig | None = None,
) -> DeliveryRuntime:
    return DeliveryRuntime(path, config)
