"""Startup recovery must yield workspace admission between attempts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from planner.conversation.deploy_interruptions import coordinator
from planner.conversation.workspace_admission import WorkspaceAdmission


def _world():
    admission = WorkspaceAdmission()
    reference = SimpleNamespace(interruption_id="interruption")
    store = SimpleNamespace(
        record_unknown_recovery_deliveries=AsyncMock(),
        select_deployment_recoveries=AsyncMock(return_value={"c": (reference,)}),
    )
    starts = SimpleNamespace(try_start=lambda request: False, finish_unbound=lambda identity: None)
    system = SimpleNamespace(
        workspace_admission=admission,
        start_admission=starts,
        automatic_start_request=AsyncMock(return_value=SimpleNamespace(work_class="worker")),
        is_running=AsyncMock(return_value=False),
        send=AsyncMock(return_value=None),
    )
    return admission, store, system


def test_update_starts_during_recovery_wait_and_prevents_retry(monkeypatch):
    async def exercise():
        admission, store, system = _world()
        waits = []
        finished = []
        system.start_admission.finish_unbound = finished.append

        async def wait(delay):
            waits.append(delay)
            assert not admission.active
            assert admission.close_starts()

        monkeypatch.setattr(coordinator.asyncio, "sleep", wait)
        await coordinator.run_automatic_recovery(system, store)
        assert waits == [1.0]
        assert system.is_running.await_count == 1
        assert system.send.await_count == 0
        assert len(finished) == 1

    asyncio.run(exercise())


def test_recovery_retries_with_backoff_and_delivers_once(monkeypatch):
    async def exercise():
        admission, store, system = _world()
        waits = []
        system.start_admission.try_start = lambda request: len(waits) >= 7

        async def wait(delay):
            assert not admission.active
            waits.append(delay)

        monkeypatch.setattr(coordinator.asyncio, "sleep", wait)
        await coordinator.run_automatic_recovery(system, store)
        assert waits == [1, 2, 4, 8, 16, 32, 60]
        assert system.send.await_count == 1
        references = system.send.await_args.kwargs["recovery_references"]
        assert references[0].interruption_id == "interruption"

    asyncio.run(exercise())


def test_update_closes_starts_during_admitted_attempt(monkeypatch):
    async def exercise():
        admission, store, system = _world()
        system.start_admission.try_start = lambda request: True
        finished = []
        system.start_admission.finish_unbound = finished.append

        async def send(*args, **kwargs):
            assert admission.active
            assert admission.close_starts()
            raise RuntimeError("Workspace admission is closed for an owner update.")

        async def wait(delay):
            assert not admission.active

        system.send = AsyncMock(side_effect=send)
        monkeypatch.setattr(coordinator.asyncio, "sleep", wait)
        await coordinator.run_automatic_recovery(system, store)
        assert system.send.await_count == 1
        assert store.select_deployment_recoveries.return_value["c"]
        assert finished

    asyncio.run(exercise())
