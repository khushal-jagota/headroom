"""Ticket completion wakes cleanup without scanning workspaces on every write."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from planner.core import change_signal
from planner.executor.mac_presence import MacLeaseLoop
from planner.ticket_workspaces.contracts import WorkspaceState
from planner.ticket_workspaces.lifecycle_monitor import monitor_lifecycle


def test_home_lifecycle_change_wakes_cleanup_but_unrelated_write_does_not() -> None:
    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        observed = asyncio.Event()
        snapshots: list[tuple[tuple[str, int, int], ...]] = [()]
        calls: list[int] = []

        def read() -> tuple[tuple[str, int, int], ...]:
            loop.call_soon_threadsafe(observed.set)
            return snapshots[0]

        async def cleanup() -> None:
            calls.append(1)

        task = asyncio.create_task(monitor_lifecycle(read, cleanup, 3600))
        try:
            await asyncio.wait_for(observed.wait(), 2)
            await asyncio.sleep(0.02)
            assert len(calls) == 1
            observed.clear()
            change_signal.emit()
            await asyncio.wait_for(observed.wait(), 2)
            await asyncio.sleep(0.02)
            assert len(calls) == 1
            observed.clear()
            snapshots[0] = (("t_aaaaaaaa", 1, 0),)
            change_signal.emit()
            await asyncio.wait_for(observed.wait(), 2)
            await asyncio.sleep(0.02)
            assert len(calls) == 2
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(exercise())


def test_mac_lifecycle_change_runs_cleanup_without_waiting_for_retry(tmp_path) -> None:
    async def exercise() -> None:
        artifacts = tmp_path / "tickets"
        (artifacts / "t_aaaaaaaa").mkdir(parents=True)
        state = WorkspaceState("t_aaaaaaaa", None, False)
        client = SimpleNamespace(workspace_states=AsyncMock())
        client.workspace_states.return_value = SimpleNamespace(states=(state,), at_vps_ms=1000)
        runner = SimpleNamespace(
            paths=SimpleNamespace(artifacts=artifacts),
            refresh_worker_cleanup=AsyncMock(),
            cleanup_workspaces=AsyncMock(),
        )
        mac = MacLeaseLoop.__new__(MacLeaseLoop)
        mac.client, mac.runner = client, runner
        mac._workspace_lifecycle = None
        mac._next_workspace_cleanup = 0
        await mac._cleanup_workspaces()
        await mac._cleanup_workspaces()
        assert runner.cleanup_workspaces.await_count == 1
        ended = WorkspaceState("t_aaaaaaaa", 2, False)
        client.workspace_states.return_value = SimpleNamespace(states=(ended,), at_vps_ms=2000)
        await mac._cleanup_workspaces()
        assert runner.cleanup_workspaces.await_count == 2
        runner.cleanup_workspaces.assert_awaited_with((ended,), 2)

    asyncio.run(exercise())


def test_home_retry_is_not_postponed_by_unrelated_changes() -> None:
    async def exercise() -> None:
        calls: list[int] = []

        async def cleanup() -> None:
            calls.append(1)

        task = asyncio.create_task(monitor_lifecycle(lambda: (), cleanup, 0.08))
        try:
            for _ in range(15):
                await asyncio.sleep(0.01)
                change_signal.emit()
            assert len(calls) >= 2
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(exercise())
