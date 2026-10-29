"""Async boundary around bounded blocking registry and Linux inspection work."""

from __future__ import annotations

import asyncio
import secrets
import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TypeVar

from planner.core.db import connect
from planner.previews import config, data
from planner.previews.contracts import PreviewError, PreviewRecord
from planner.previews.logic.eligibility import require_eligible, require_port
from planner.previews.process import LinuxProcessInspector, validate_worktree

T = TypeVar("T")


class PreviewService:
    def __init__(self, db_path: str, public_origin: str, panels_port: int = 8767) -> None:
        self.db_path = db_path
        self.public_origin = public_origin.rstrip("/")
        self.panels_port = panels_port
        self.server_epoch = secrets.token_hex(16)
        self.inspector = LinuxProcessInspector()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, config.INSPECTION_WORKERS - 1),
            thread_name_prefix="preview-inspection",
        )
        self._lifecycle_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="preview-lifecycle"
        )
        self._lock = threading.Lock()
        self._registration = threading.Lock()
        self._outstanding = 0
        self._closed = False
        self._known: set[str] = set()
        self._process_checks: dict[str, asyncio.Task[None]] = {}

    async def _run(self, operation: Callable[[], T], *, lifecycle: bool = False) -> T:
        with self._lock:
            if self._closed or self._outstanding >= (
                config.INSPECTION_OUTSTANDING
                if lifecycle
                else max(1, config.INSPECTION_OUTSTANDING - 1)
            ):
                raise PreviewError("Preview inspection is busy; try again shortly.", 503)
            self._outstanding += 1
        submitted_at = time.monotonic()

        def work() -> T:
            if time.monotonic() - submitted_at >= config.INSPECTION_SECONDS:
                raise PreviewError("Preview inspection admission expired.", 503)
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                # Extended SQLite result codes retain their primary code below.
                # A transient writer must not kill the lifecycle monitor.
                if getattr(exc, "sqlite_errorcode", 0) & 0xFF in {
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                }:
                    raise PreviewError("Preview registry is busy; try again shortly.", 503) from exc
                raise

        executor = self._lifecycle_executor if lifecycle else self._executor
        future = executor.submit(work)

        def done(_: object) -> None:
            with self._lock:
                self._outstanding -= 1

        future.add_done_callback(done)
        return await asyncio.shield(asyncio.wrap_future(future))

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.db_path, busy_timeout_ms=config.DATABASE_BUSY_MILLISECONDS)
        try:
            yield conn
        finally:
            conn.close()

    def _enabled(self) -> None:
        if not self.public_origin.startswith("https://"):
            raise PreviewError(
                "Application previews require configured trusted Tailscale ingress.", 503
            )

    async def start(self) -> None:
        def invalidate() -> None:
            with self._connection() as conn:
                conn.execute(
                    f"UPDATE {data.TABLE} SET state='ended', "
                    "ended_reason='Panels restarted; prepare a new address.' "
                    "WHERE state!='ended' AND server_epoch!=?",
                    (self.server_epoch,),
                )

        await self._run(invalidate)

    async def prepare(self, ticket_id: str) -> PreviewRecord:
        self._enabled()

        def operation() -> PreviewRecord:
            with self._connection() as conn:
                ticket = data.eligibility(conn, ticket_id)
                require_eligible(ticket)
                assert ticket.project_folder is not None
                worktree = validate_worktree(ticket.project_folder, ticket_id)
                record = PreviewRecord(
                    ticket_id,
                    secrets.token_hex(16),
                    str(worktree),
                    self.public_origin,
                    self.server_epoch,
                    int(time.time()),
                    "prepared",
                )
                data.prepare(conn, record)
                return record

        record = await self._run(operation)
        self._known.add(record.generation)
        return record

    def _check(
        self, conn: sqlite3.Connection, ticket_id: str, generation: str, *, active: bool = True
    ) -> PreviewRecord:
        record = data.get(conn, ticket_id, generation)
        if record is None or record.state == "ended":
            raise PreviewError(
                record.ended_reason
                if record and record.ended_reason
                else "This preview address has expired.",
                410,
            )
        try:
            if record.server_epoch != self.server_epoch:
                raise PreviewError("Panels restarted; prepare a new address.", 410)
            ticket = data.eligibility(conn, ticket_id)
            require_eligible(ticket)
            assert ticket.project_folder is not None
            expected = ticket.project_folder.absolute() / "worktrees" / f"{ticket_id}-preview"
            if str(expected) != record.worktree or expected.resolve(strict=True) != expected:
                raise PreviewError("The Ticket preview worktree changed.", 410)
            if active:
                if record.state != "active" or record.process is None:
                    raise PreviewError("The preview application has not been registered.", 410)
                self.inspector.check(record.process)
        except OSError as exc:
            data.end(conn, generation, "The Ticket preview worktree is unavailable.")
            raise PreviewError("The Ticket preview worktree is unavailable.", 410) from exc
        except PreviewError as exc:
            # Busy inspections do not establish process loss.
            if exc.status_code != 503:
                data.end(conn, generation, str(exc))
            raise
        return record

    async def register(self, ticket_id: str, port: int) -> PreviewRecord:
        self._enabled()
        require_port(port, self.panels_port)

        def operation() -> PreviewRecord:
            if not self._registration.acquire(blocking=False):
                raise PreviewError("Another preview registration is being inspected.", 503)
            try:
                with self._connection() as conn:
                    current = data.get(conn, ticket_id)
                    if current is None or current.state != "prepared":
                        raise PreviewError("Prepare a new preview address before registration.")
                    self._check(conn, ticket_id, current.generation, active=False)
                    process = self.inspector.discover(port, Path(current.worktree))
                    self._check(conn, ticket_id, current.generation, active=False)
                    self.inspector.check(process)
                    data.register(conn, current, process)
                    return replace(current, state="active", process=process)
            finally:
                self._registration.release()

        return await self._run(operation)

    async def show(self, ticket_id: str) -> PreviewRecord | None:
        def operation() -> PreviewRecord | None:
            with self._connection() as conn:
                record = data.get(conn, ticket_id)
                if record is not None and record.state != "ended":
                    try:
                        self._check(
                            conn, ticket_id, record.generation, active=record.state == "active"
                        )
                    except PreviewError as exc:
                        if exc.status_code != 410:
                            raise
                    record = data.get(conn, ticket_id)
                return record

        return await self._run(operation)

    async def stop(self, ticket_id: str) -> PreviewRecord | None:
        def operation() -> PreviewRecord | None:
            with self._connection() as conn:
                record = data.get(conn, ticket_id)
                if record:
                    data.end(conn, record.generation, "The preview was stopped.")
                return data.get(conn, ticket_id)

        return await self._run(operation)

    async def check(self, ticket_id: str, generation: str) -> PreviewRecord:
        def operation() -> PreviewRecord:
            with self._connection() as conn:
                return self._check(conn, ticket_id, generation)

        return await self._run(operation)

    async def before_connection(self, record: PreviewRecord) -> None:
        await self.check(record.ticket_id, record.generation)

    async def after_connection(self, record: PreviewRecord, local_port: int) -> None:
        def operation() -> None:
            with self._connection() as conn:
                current = self._check(conn, record.ticket_id, record.generation)
                assert current.process is not None
                try:
                    self.inspector.connected(current.process, local_port)
                except PreviewError as exc:
                    if exc.status_code != 503:
                        data.end(conn, current.generation, str(exc))
                    raise
                self._check(conn, record.ticket_id, record.generation)

        await self._run(operation)

    async def verify_connection(
        self, record: PreviewRecord, connected_socket: socket.socket
    ) -> None:
        await self.after_connection(record, int(connected_socket.getsockname()[1]))

    async def recheck_all(self) -> tuple[str, ...]:
        # Canonical transitions are checked before scheduling any slow process scan.
        # They must never wait behind a sequence of one-second process inspections.
        def canonical() -> list[PreviewRecord]:
            with self._connection() as conn:
                for record in data.live(conn):
                    try:
                        self._check(conn, record.ticket_id, record.generation, active=False)
                    except PreviewError:
                        pass
                return data.live(conn)

        records = await self._run(canonical, lifecycle=True)
        current = {record.generation for record in records}
        ended = tuple(self._known - current)
        self._known = current

        async def inspect(record: PreviewRecord) -> None:
            try:
                await self.check(record.ticket_id, record.generation)
            except PreviewError:
                # Definite failure is already persisted. Saturation is transient.
                pass

        for record in records:
            previous = self._process_checks.get(record.generation)
            if record.state == "active" and (previous is None or previous.done()):
                self._process_checks[record.generation] = asyncio.create_task(inspect(record))
        for generation in tuple(self._process_checks):
            task = self._process_checks[generation]
            if generation not in current and task.done():
                del self._process_checks[generation]
        return ended

    async def close(self) -> None:
        for task in self._process_checks.values():
            task.cancel()
        await asyncio.gather(*self._process_checks.values(), return_exceptions=True)
        with self._lock:
            self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
        await asyncio.to_thread(self._lifecycle_executor.shutdown, wait=True, cancel_futures=True)
