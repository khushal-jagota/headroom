"""Protect irreversible generation expiry and bounded off-loop inspection admission."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from planner.previews import config, data
from planner.previews.contracts import (
    PreviewError,
    PreviewRecord,
    ProcessIdentity,
    TicketEligibility,
)
from planner.previews.logic.eligibility import require_eligible, require_port
from planner.previews.service import PreviewService


def record(generation: str, ticket_id: str = "t_test") -> PreviewRecord:
    return PreviewRecord(
        ticket_id,
        generation,
        "/project/worktrees/t_test-preview",
        "https://panels.example",
        "epoch",
        1,
        "prepared",
    )


def test_generation_replacement_and_port_exclusion_are_persistent(
    tmp_db: sqlite3.Connection,
) -> None:
    first = record("first")
    data.prepare(tmp_db, first)
    process = ProcessIdentity("boot", 12, 34, 56, 3001, first.worktree)
    data.register(tmp_db, first, process)
    second = record("second", "t_other")
    data.prepare(tmp_db, second)
    with pytest.raises(PreviewError, match="another Ticket"):
        data.register(tmp_db, second, process)
    data.prepare(tmp_db, record("replacement"))
    assert data.get(tmp_db, "t_test", "first").state == "ended"  # type: ignore[union-attr]
    with pytest.raises(PreviewError, match="ended"):
        data.register(tmp_db, first, process)
    data.register(tmp_db, second, process)


def test_terminal_ticket_and_panels_ports_fail_closed() -> None:
    with pytest.raises(PreviewError, match="finished"):
        require_eligible(TicketEligibility("t_test", Path("/project"), True))
    for port in (80, 8767, 8768, 65536):
        with pytest.raises(PreviewError):
            require_port(port, 8768)


def test_restart_ends_prepared_generations(tmp_db: sqlite3.Connection, tmp_path: Path) -> None:
    data.prepare(tmp_db, record("old"))
    path = str(tmp_path / "planning-test.db")

    async def exercise() -> None:
        service = PreviewService(path, "https://panels.example")
        await service.start()
        await service.close()

    asyncio.run(exercise())
    expired = data.get(tmp_db, "t_test", "old")
    assert expired is not None and expired.state == "ended"
    assert expired.ended_reason and "restarted" in expired.ended_reason


def test_cancelled_inspection_keeps_capacity_until_thread_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "INSPECTION_OUTSTANDING", 1)
    entered, release = threading.Event(), threading.Event()

    def blocking() -> None:
        entered.set()
        assert release.wait(2)

    async def exercise() -> None:
        service = PreviewService("/unused", "https://panels.example")
        task = asyncio.create_task(service._run(blocking))
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(PreviewError, match="busy"):
            await service._run(lambda: None)
        release.set()
        await service.close()

    asyncio.run(exercise())


def test_lifecycle_lane_runs_while_process_inspection_is_blocked_and_recovers_sqlite_lock(
    tmp_path: Path,
) -> None:
    entered, release = threading.Event(), threading.Event()

    def blocking() -> None:
        entered.set()
        assert release.wait(2)

    db_path = tmp_path / "locked.db"
    writer = sqlite3.connect(db_path, isolation_level=None)
    writer.execute("CREATE TABLE busy_test(value INTEGER)")
    writer.execute("BEGIN IMMEDIATE")

    def write_value() -> None:
        with sqlite3.connect(db_path, timeout=0.01) as connection:
            connection.execute("INSERT INTO busy_test VALUES (1)")

    def broken_query() -> None:
        with sqlite3.connect(db_path) as connection:
            connection.execute("SELECT * FROM nonexistent")

    async def exercise() -> None:
        service = PreviewService("/unused", "https://panels.example")
        task = asyncio.create_task(service._run(blocking))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            value = await asyncio.wait_for(service._run(lambda: "canonical", lifecycle=True), 0.2)
            assert value == "canonical"
            with pytest.raises(PreviewError, match="registry is busy") as busy:
                await service._run(write_value, lifecycle=True)
            assert busy.value.status_code == 503
            writer.rollback()
            await service._run(write_value, lifecycle=True)
            with pytest.raises(sqlite3.OperationalError, match="nonexistent"):
                await service._run(broken_query, lifecycle=True)
        finally:
            release.set()
            await task
            await service.close()

    try:
        asyncio.run(exercise())
    finally:
        writer.close()


def test_mac_commands_and_retained_preview_never_inspect_vps_processes(
    tmp_db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.principals import OWNER_PRINCIPAL

    from planner.tickets.data import create_ticket

    ticket = create_ticket(
        tmp_db, title="Mac preview refusal", worker_type="coding", principal=OWNER_PRINCIPAL,
        now=1, title_max_chars=200,
    )
    prepared = record("was-on-vps", ticket.id)
    data.prepare(tmp_db, prepared)
    data.register(tmp_db, prepared, ProcessIdentity("boot", 12, 34, 56, 3001, prepared.worktree))
    tmp_db.execute("UPDATE tickets SET locale='macos' WHERE id=?", (ticket.id,))
    tmp_db.commit()

    def no_vps_process(*args: object, **kwargs: object) -> None:
        raise AssertionError("Mac preview must not inspect or connect to a VPS process")

    async def exercise() -> None:
        service = PreviewService(str(tmp_path / "planning-test.db"), "https://panels.example")
        service.server_epoch = "epoch"
        monkeypatch.setattr(service.inspector, "check", no_vps_process)
        monkeypatch.setattr(service.inspector, "discover", no_vps_process)
        try:
            operations: tuple[Callable[[], Awaitable[object]], ...] = (
                lambda: service.prepare(ticket.id),
                lambda: service.register(ticket.id, 3001),
                lambda: service.show(ticket.id),
                lambda: service.stop(ticket.id),
                lambda: service.check(ticket.id, prepared.generation),
            )
            for operation in operations:
                with pytest.raises(PreviewError, match="unavailable.*Mac"):
                    await operation()
            ended = data.get(tmp_db, ticket.id, prepared.generation)
            assert ended is not None and ended.state == "ended"
        finally:
            await service.close()

    asyncio.run(exercise())
