"""The in-server stall checker against a real database, with fake stall sources.

Risk: findings that never reach the table the watchdog reads (or never clear), a check
that fails without the heartbeat showing it, a restart that re-dates or drops an open
stall, and a VPS save retrying forever with nothing tracking it. Nothing covered the
checker before this change. Layer: StallChecker over a fresh schema in tmp_path, with
fake sources standing in for Part B's VpsExecutor.stall_sources(); the rule itself is
proven in test_stall_findings.py.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from tests.support.ticket_workflows import preset_workflow_json

from planner.conversation import storage
from planner.conversation.storage import ConversationStore
from planner.core.db import connect, create_schema
from planner.execution_identity import ExecutorIdentity
from planner.executor.protocol import LeaseRequest, WaitingConversation
from planner.executor.vps_presence_store import accept_lease_in_transaction
from planner.stalls import configuration
from planner.stalls.checker import StallChecker
from planner.stalls.contracts import StallSource, TrackedWait, TrackedWaitKind
from planner.stalls.registry import WAITS, ProcessWaitRegistry

S = 1000


class _Clock:
    def __init__(self) -> None:
        self.now_ms = 0

    def __call__(self) -> int:
        return self.now_ms


def _database(tmp_path: Path) -> str:
    path = str(tmp_path / "planner.db")
    conn = connect(path)
    try:
        create_schema(conn)
        conn.execute(
            "INSERT INTO tickets (id,title,worker_type,employee_backend,ceiling,field_values,"
            "worker_step_claim,stage,created_at,updated_at,workflow_json,conversation_id) "
            "VALUES ('t_login','Fix the login bug','coding','claude','needs_brief','{}','out',"
            "'needs_brief',1,1,?,'c1')",
            (preset_workflow_json(conn),),
        )
    finally:
        conn.close()
    return path


def _rows(db_path: str) -> dict[str, sqlite3.Row]:
    conn = connect(db_path)
    try:
        return {
            str(row["finding_key"]): row
            for row in conn.execute("SELECT * FROM stall_findings ORDER BY finding_key")
        }
    finally:
        conn.close()


def _heartbeat(db_path: str) -> int | None:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT checked_at_ms FROM stall_checker_heartbeat").fetchone()
        return None if row is None else int(row[0])
    finally:
        conn.close()


def _lease(db_path: str, now_ms: int, oldest_sequence: int) -> None:
    lease = LeaseRequest(
        identity=ExecutorIdentity(22, "rev", 5, "incarnation-1"),
        process_id=1,
        mac_wall_clock_ms=now_ms,
        queue_depth=1,
        oldest_item_age_ms=None,
        waiting_conversations=(WaitingConversation("c1", oldest_sequence),),
        waiting_conversations_truncated=False,
        working_turns=(),
        admission_stopped=False,
    )
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        accept_lease_in_transaction(conn, lease, now_ms)
        conn.execute("COMMIT")
    finally:
        conn.close()


class _UnfetchedCommands:
    """Like B's mailbox source: waiting executor_commands rows, read from the database."""

    def tracked_waits(self, conn: sqlite3.Connection, now_ms: int) -> tuple[TrackedWait, ...]:
        return tuple(
            TrackedWait(
                TrackedWaitKind.mac_command_unfetched,
                str(row[0]),
                row[1],
                f"command {row[0]} is waiting",
                int(row[2]),
            )
            for row in conn.execute(
                "SELECT command_id,conversation_id,created_at_ms FROM executor_commands "
                "WHERE state='waiting'"
            )
        )


def _put_command(db_path: str, created_at_ms: int) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO executor_commands(command_id,executor_incarnation,operation,"
            "conversation_id,command_json,state,created_at_ms) "
            "VALUES ('cmd-1','incarnation-1','write_prompt','c1','{}','waiting',?)",
            (created_at_ms,),
        )
    finally:
        conn.close()


def test_a_check_writes_named_findings_and_the_heartbeat_and_clears_what_moved(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    clock = _Clock()
    registry = ProcessWaitRegistry(clock)
    checker = StallChecker(db_path, registry, lambda: (), clock)

    async def scenario() -> None:
        with registry.track(
            TrackedWaitKind.vps_save_retrying,
            "c1",
            conversation_id="c1",
            detail="stuck at write 4",
        ):
            clock.now_ms = configuration.VPS_SAVE_RETRY_LIMIT_SECONDS * S - 1
            assert await checker.check_once() == ()
            clock.now_ms += 1
            (finding,) = await checker.check_once()
            assert (finding.ticket_id, finding.ticket_title) == ("t_login", "Fix the login bug")
        clock.now_ms += 15 * S
        assert await checker.check_once() == ()

    asyncio.run(scenario())
    row = _rows(db_path)["vps_save_retrying:c1"]
    assert (row["conversation_id"], row["detail"], row["stalled_since_ms"]) == (
        "c1",
        "stuck at write 4",
        0,
    )
    assert row["cleared_at_ms"] == clock.now_ms
    assert _heartbeat(db_path) == clock.now_ms


def test_open_stalls_keep_their_start_across_a_checker_restart(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    clock = _Clock()
    sources: tuple[StallSource, ...] = (_UnfetchedCommands(),)
    _put_command(db_path, created_at_ms=0)

    async def checks(checker: StallChecker, until_ms: int) -> None:
        while clock.now_ms <= until_ms:
            _lease(db_path, clock.now_ms, oldest_sequence=7)
            await checker.check_once()
            clock.now_ms += configuration.STALL_CHECK_INTERVAL_SECONDS * S

    asyncio.run(
        checks(StallChecker(db_path, ProcessWaitRegistry(clock), lambda: sources, clock), 180 * S)
    )
    before = _rows(db_path)
    assert set(before) == {"mac_command_unfetched:cmd-1", "mac_items_not_moving:c1:7"}
    # A new server process: a fresh checker with no memory of the leases it saw.
    asyncio.run(
        checks(StallChecker(db_path, ProcessWaitRegistry(clock), lambda: sources, clock), 195 * S)
    )
    after = _rows(db_path)
    for key, row in before.items():
        assert after[key]["cleared_at_ms"] is None
        assert after[key]["stalled_since_ms"] == row["stalled_since_ms"] == 0
        assert after[key]["ticket_title"] == "Fix the login bug"


def test_the_loop_runs_on_its_interval_and_a_failing_check_writes_no_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _database(tmp_path)
    monkeypatch.setattr(configuration, "STALL_CHECK_INTERVAL_SECONDS", 0.01)
    clock = _Clock()
    broken = True

    class _Source:
        def tracked_waits(self, conn: sqlite3.Connection, now_ms: int) -> tuple[TrackedWait, ...]:
            if broken:
                raise RuntimeError("source failed")
            return ()

    async def scenario() -> None:
        nonlocal broken
        checker = StallChecker(db_path, ProcessWaitRegistry(clock), lambda: (_Source(),), clock)
        checker.start()
        try:
            await asyncio.sleep(0.2)
            assert _heartbeat(db_path) is None
            broken = False
            clock.now_ms = 5 * S
            for _ in range(200):
                if _heartbeat(db_path) == 5 * S:
                    break
                await asyncio.sleep(0.01)
        finally:
            await checker.close()
        clock.now_ms = 9 * S
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    assert _heartbeat(db_path) == 5 * S


def test_a_vps_save_that_keeps_failing_is_tracked_until_it_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "CONVERSATION_WRITE_FAILURE_FIRST_RETRY_SECONDS", 0)
    store = ConversationStore(str(tmp_path / "unused.db"))
    seen: list[tuple[TrackedWait, ...]] = []

    async def operation() -> str:
        seen.append(tuple(w for w in WAITS.snapshot() if w.conversation_id == "c-save"))
        if len(seen) < 3:
            raise sqlite3.DatabaseError("disk I/O error")
        return "saved"

    assert asyncio.run(store.settle_recording("c-save", operation)) == "saved"
    assert seen[0] == ()
    assert [(w.kind, w.wait_key, w.detail) for w in seen[2]] == [
        (TrackedWaitKind.vps_save_retrying, "c-save", "Stuck at write 2")
    ]
    assert not [w for w in WAITS.snapshot() if w.conversation_id == "c-save"]
