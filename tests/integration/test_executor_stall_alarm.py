"""Pass/fail condition 2: any stall raises an alarm within 5 minutes (contract §8.2).

Risk: a conversation stuck for real is never reported, or reported without naming
what is stuck, or reported on every watchdog run; or a Mac that is merely away (asleep,
or its link gone) with output still waiting raises a false alarm.
Gap: test_stall_findings.py proves the rule and the 300 s arithmetic with production
constants, test_stall_checker.py the checker over hand-written rows, and
test_stall_watchdog.py the watchdog over hand-written findings. None of them starts
from a real stuck conversation: the Mac's real leases naming its waiting item, the
VPS's real still-saving wait, the real stall sources of a running VpsExecutor.
Layer: integration, over real HTTPS through serve_executor_app. Setup reused: boot and
ExecutorHarness from test_executor_end_to_end, held_mac_writes from
test_executor_isolation (the write is held before its transaction opens), and the fake
push adapter and Chief from tests/support/stall_alarm_fakes. The limits are scaled down by
monkeypatch (stalls.configuration is read at call time); the checker runs through
check_once instead of its 15 s timer, and the watchdog through run_watchdog.
Cost: about 5 s of test time, plus about 1 s of boot (12 s wall for the file alone).
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest
from tests.integration.test_executor_end_to_end import (
    ExecutorHarness,
    boot,
    eventually,
    mac_unreachable,
)
from tests.integration.test_executor_isolation import held_mac_writes
from tests.support.stall_alarm_fakes import FakeChief, FakeWebPush

from planner.conversation.backends.contracts import ToolCallStarted
from planner.core.db import connect
from planner.executor.clock import now_ms
from planner.executor.vps_presence_store import read_presence
from planner.notifications import data as notifications_data
from planner.stalls.checker import StallChecker
from planner.stalls.contracts import StallFinding, TrackedWait, TrackedWaitKind
from planner.stalls.registry import WAITS
from planner.stalls.watchdog import WatchdogRun, run_watchdog

pytestmark = pytest.mark.usefixtures("executor_transport_home")

STALL_LIMIT_SECONDS = 2
CONTINUITY_SECONDS = 1.0


def leased_backlog(db_path: str) -> tuple[str, ...]:
    """The conversations the Mac's last accepted lease says have items waiting."""
    connection = connect(db_path)
    try:
        record = read_presence(connection)
    finally:
        connection.close()
    assert record is not None and record.lease is not None
    return tuple(waiting.conversation_id for waiting in record.lease.waiting_conversations)


class WaitsSince:
    """The process-wide wait registry, seen from one moment on. Another test on the
    same worker that left a wait behind cannot then be mistaken for this one's."""

    def __init__(self, since_ms: int) -> None:
        self.since_ms = since_ms
        self.track = WAITS.track

    def snapshot(self) -> tuple[TrackedWait, ...]:
        return tuple(wait for wait in WAITS.snapshot() if wait.started_at_ms >= self.since_ms)


def last_lease_at_ms(db_path: str) -> int:
    connection = connect(db_path)
    try:
        record = read_presence(connection)
    finally:
        connection.close()
    assert record is not None and record.last_lease_at_ms is not None
    return record.last_lease_at_ms


def watch(
    harness: ExecutorHarness, chief: FakeChief, web_push: FakeWebPush, at_ms: int
) -> WatchdogRun:
    return run_watchdog(
        harness.endpoint.db_path,
        harness.root / "watchdog-state",
        now_ms=at_ms,
        web_push=web_push,
        apns=None,
        push_origin=None,
        message_chief=chief,
    )


def test_a_stuck_conversation_alarms_once_and_an_away_mac_with_the_same_backlog_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS", "MAC_ITEM_SETTLING_LIMIT_SECONDS"):
        monkeypatch.setattr(f"planner.stalls.configuration.{name}", STALL_LIMIT_SECONDS)
    # Leases every 0.2 s instead of 10 s, so the presence row follows the Mac closely.
    monkeypatch.setattr("planner.executor.mac_presence.LEASE_INTERVAL_SECONDS", 0.2)
    # A gap of 1 s between leases (not 25 s) already breaks continuous presence.
    monkeypatch.setattr(
        "planner.executor.logic.presence.LEASE_CONTINUITY_SECONDS", CONTINUITY_SECONDS
    )
    monkeypatch.setattr("planner.executor.vps_inbox.MAC_ITEM_RETRY_LATER_SECONDS", 0.5)

    async def run() -> None:
        started_ms = now_ms()
        harness = await boot(tmp_path, monkeypatch)
        try:
            db_path = harness.endpoint.db_path
            with contextlib.closing(connect(db_path)) as connection:
                notifications_data.register_subscription(
                    connection, endpoint="https://push.example/1", p256dh="key", auth="auth", now=1
                )
            waits = WaitsSince(started_ms)
            checker = StallChecker(db_path, waits, harness.endpoint.stall_sources, now_ms)
            chief, web_push = FakeChief(lambda text: True), FakeWebPush()
            assert await checker.check_once() == ()

            with held_mac_writes(harness, "c_scripted") as (entered, release):
                native = harness.native
                assert native.sink is not None and native.turn is not None
                await native.sink.report(
                    native.turn,
                    ToolCallStarted(
                        tool_call_id="stuck", title="Stuck", tool_kind="other", detail=None
                    ),
                )
                await asyncio.wait_for(entered.wait(), 5)

                # Stuck past the limit: the checker reports it, naming the conversation
                # and its Ticket.
                findings: tuple[StallFinding, ...] = ()
                deadline = asyncio.get_running_loop().time() + STALL_LIMIT_SECONDS + 5
                while {finding.kind for finding in findings} != {
                    TrackedWaitKind.mac_items_not_moving,
                    TrackedWaitKind.mac_item_still_saving,
                }:
                    assert asyncio.get_running_loop().time() < deadline, findings
                    await asyncio.sleep(0.2)
                    findings = await checker.check_once()
                for finding in findings:
                    assert finding.conversation_id == "c_scripted"
                    assert finding.ticket_id == harness.ticket_id
                    assert finding.ticket_title == "Reconnect worker"

                # The watchdog pushes and tells the Chief once, and stays quiet after.
                first = watch(harness, chief, web_push, now_ms())
                assert len(first.raised) == 2 and first.pushes_delivered == 1
                (pushed,) = web_push.sent
                assert 'Ticket "Reconnect worker" (conversation c_scripted)' in pushed.body
                assert pushed.route == "/#/"  # two alarms, so no single Ticket to open
                assert len(chief.messages) == 1
                assert "c_scripted" in chief.messages[0]
                assert await checker.check_once()
                assert watch(harness, chief, web_push, now_ms()).raised == ()
                assert len(web_push.sent) == len(chief.messages) == 1

                # Now the Mac goes away with the same backlog, and the VPS's save ends.
                assert "c_scripted" in leased_backlog(db_path)
                async with mac_unreachable(harness.proxy):
                    release.set()
                    await eventually(
                        lambda: (
                            harness.events("tool_call_started") == 1
                            and not any(
                                wait.conversation_id == "c_scripted" for wait in waits.snapshot()
                            )
                        ),
                        "the held save did not finish once released",
                    )
                    assert harness.mac.outbox.pending()
                    assert "c_scripted" in leased_backlog(db_path)
                    # No lease gets through. Once the gap passes the continuity limit
                    # the Mac may be asleep, and the Mac-dependent rules pause.
                    await eventually(
                        lambda: (
                            now_ms() - last_lease_at_ms(db_path) > (CONTINUITY_SECONDS + 0.5) * 1000
                        ),
                        "a lease still got through",
                    )
                    assert await checker.check_once() == ()
                    assert watch(harness, chief, web_push, now_ms()).due == ()
                    assert len(web_push.sent) == len(chief.messages) == 1
            # Back again, the resend finds its receipt: applied once.
            await harness.wait_settled("the backlog did not drain after the Mac returned")
            assert harness.events("tool_call_started") == 1
        finally:
            await harness.close()

    asyncio.run(run())
