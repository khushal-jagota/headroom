"""The in-server stall checker: one loop over every tracked wait.

Every STALL_CHECK_INTERVAL_SECONDS it gathers the in-process registry, the stall
sources and the presence row, applies stalls.logic.findings, and writes stall_findings
and the heartbeat in one quiet commit. The watchdog unit reads both with the database
opened read-only, so a stuck server still raises an alarm: its heartbeat goes stale.

The sources read database rows, so their waits keep their starts across a restart. The
item and lease-failing observations are rebuilt from the open findings at the first
check: an overdue stall stays overdue from its first start, but one not yet overdue
when the server restarted counts again from the restart (a known gap).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable

from planner.core.db import commit_without_change_signal, connect
from planner.core.task_settlement import run_on_worker_thread
from planner.executor.vps_presence_store import read_presence
from planner.stalls import configuration
from planner.stalls.contracts import (
    StallFinding,
    StallSource,
    TrackedWait,
    TrackedWaitKind,
    WaitRegistry,
)
from planner.stalls.logic.findings import (
    OverdueWait,
    PresenceObservation,
    observation_from_open_findings,
    overdue_waits,
    presence_waits,
)

_LOG = logging.getLogger(__name__)


def read_open_findings(conn: sqlite3.Connection) -> tuple[StallFinding, ...]:
    """Open findings of the kinds this build knows (a row an older or newer build wrote
    with another kind is skipped, so it cannot stop the watchdog's other alarms)."""
    known = [kind.value for kind in TrackedWaitKind]
    return tuple(
        StallFinding(
            finding_key=str(row[0]),
            kind=TrackedWaitKind(row[1]),
            conversation_id=row[2],
            ticket_id=row[3],
            ticket_title=row[4],
            detail=str(row[5]),
            stalled_since_ms=int(row[6]),
            last_seen_at_ms=int(row[7]),
            cleared_at_ms=None,
        )
        for row in conn.execute(
            "SELECT finding_key,kind,conversation_id,ticket_id,ticket_title,detail,"
            "stalled_since_ms,last_seen_at_ms FROM stall_findings "
            "WHERE cleared_at_ms IS NULL AND kind IN (SELECT value FROM json_each(?)) "
            "ORDER BY stalled_since_ms,finding_key",
            (json.dumps(known),),
        )
    )


class StallChecker:
    """Satisfies stalls.contracts.StallChecker; its constructor is the factory."""

    def __init__(
        self,
        db_path: str,
        registry: WaitRegistry,
        sources: Callable[[], tuple[StallSource, ...]],
        clock: Callable[[], int],
    ) -> None:
        self._db_path = db_path
        self._registry = registry
        self._sources = sources
        self._clock = clock
        self._observation: PresenceObservation | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("stall checker already started")
        self._task = asyncio.get_running_loop().create_task(self._run(), name="stall-checker")

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise

    async def check_once(self) -> tuple[StallFinding, ...]:
        """One check. Returns the findings left open."""
        now_ms = self._clock()
        registry_waits = self._registry.snapshot()
        sources = self._sources()
        return await run_on_worker_thread(self._check, now_ms, registry_waits, sources)

    async def _run(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception:
                # No heartbeat is written, so a check that keeps failing alarms too.
                _LOG.exception("stall check failed")
            await asyncio.sleep(configuration.STALL_CHECK_INTERVAL_SECONDS)

    def _check(
        self,
        now_ms: int,
        registry_waits: tuple[TrackedWait, ...],
        sources: tuple[StallSource, ...],
    ) -> tuple[StallFinding, ...]:
        conn = connect(self._db_path)
        try:
            conn.execute("BEGIN")
            try:
                record = read_presence(conn)
                source_waits = [
                    wait for source in sources for wait in source.tracked_waits(conn, now_ms)
                ]
                if self._observation is None:
                    self._observation = observation_from_open_findings(read_open_findings(conn))
            finally:
                conn.execute("ROLLBACK")
            lease_waits, observation = presence_waits(record, now_ms, self._observation)
            overdue = overdue_waits((*registry_waits, *source_waits, *lease_waits), record, now_ms)
            conn.execute("BEGIN IMMEDIATE")
            try:
                findings = _write_findings(conn, overdue, now_ms)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            # No screen shows findings; the watchdog reads them on its own timer.
            commit_without_change_signal(conn)
            self._observation = observation
            return findings
        finally:
            conn.close()


def _write_findings(
    conn: sqlite3.Connection, overdue: tuple[OverdueWait, ...], now_ms: int
) -> tuple[StallFinding, ...]:
    tickets = _tickets_by_conversation(
        conn, {item.wait.conversation_id for item in overdue if item.wait.conversation_id}
    )
    findings = []
    for item in overdue:
        wait = item.wait
        ticket_id, ticket_title = tickets.get(wait.conversation_id or "", (None, None))
        findings.append(
            StallFinding(
                finding_key=wait.finding_key,
                kind=wait.kind,
                conversation_id=wait.conversation_id,
                ticket_id=ticket_id,
                ticket_title=ticket_title,
                detail=wait.detail,
                stalled_since_ms=item.stalled_since_ms,
                last_seen_at_ms=now_ms,
                cleared_at_ms=None,
            )
        )
    conn.executemany(
        "INSERT INTO stall_findings(finding_key,kind,conversation_id,ticket_id,ticket_title,"
        "detail,stalled_since_ms,last_seen_at_ms,cleared_at_ms) VALUES (?,?,?,?,?,?,?,?,NULL) "
        "ON CONFLICT(finding_key) DO UPDATE SET kind=excluded.kind,"
        "conversation_id=excluded.conversation_id,ticket_id=excluded.ticket_id,"
        "ticket_title=excluded.ticket_title,detail=excluded.detail,"
        "stalled_since_ms=excluded.stalled_since_ms,last_seen_at_ms=excluded.last_seen_at_ms,"
        "cleared_at_ms=NULL",
        [
            (
                finding.finding_key,
                finding.kind.value,
                finding.conversation_id,
                finding.ticket_id,
                finding.ticket_title,
                finding.detail,
                finding.stalled_since_ms,
                finding.last_seen_at_ms,
            )
            for finding in findings
        ],
    )
    open_keys = [finding.finding_key for finding in findings]
    conn.execute(
        "UPDATE stall_findings SET cleared_at_ms=? WHERE cleared_at_ms IS NULL "
        "AND finding_key NOT IN (SELECT value FROM json_each(?))",
        (now_ms, _json_list(open_keys)),
    )
    conn.execute(
        "INSERT INTO stall_checker_heartbeat(singleton,checked_at_ms) VALUES (1,?) "
        "ON CONFLICT(singleton) DO UPDATE SET checked_at_ms=excluded.checked_at_ms",
        (now_ms,),
    )
    return tuple(findings)


def _tickets_by_conversation(
    conn: sqlite3.Connection, conversation_ids: set[str]
) -> dict[str, tuple[str, str]]:
    if not conversation_ids:
        return {}
    return {
        str(row[0]): (str(row[1]), str(row[2]))
        for row in conn.execute(
            "SELECT conversation_id,id,title FROM tickets "
            "WHERE conversation_id IN (SELECT value FROM json_each(?))",
            (_json_list(sorted(conversation_ids)),),
        )
    }


def _json_list(values: list[str]) -> str:
    return json.dumps(values)
