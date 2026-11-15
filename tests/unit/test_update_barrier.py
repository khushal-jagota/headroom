"""The output cut must include saves that outlive their HTTP requests."""

import asyncio
from pathlib import Path

import pytest

from planner.core.errors import PlannerError
from planner.environments.update_barrier import UpdateBarrier
from planner.executor.vps_transport import VpsExecutor


def test_barrier_survives_restart_outside_database(tmp_path: Path) -> None:
    barrier = UpdateBarrier(tmp_path / "update-barrier.json")
    barrier.freeze("attempt")
    replacement = UpdateBarrier(barrier.path)
    with replacement.operation() as admitted:
        assert not admitted
    replacement.release()
    barrier.refresh()
    assert not barrier.frozen
    # Late cancellation cleanup cannot remove a newer update's marker.
    canceled = barrier.close()
    released = barrier.reopen()
    barrier.refresh()
    assert not barrier.frozen
    barrier.persist("canceled", canceled)
    assert not barrier.path.exists()
    current = barrier.close()
    barrier.persist("current", current)
    barrier.remove(released)
    assert barrier.path.read_text().strip() == "current"


def test_cut_waits_for_detached_save_and_refuses_later_receipts(tmp_path: Path) -> None:
    async def run() -> None:
        barrier = UpdateBarrier(tmp_path / "update-barrier.json")
        endpoint = object.__new__(VpsExecutor)
        endpoint.update_barrier = barrier
        endpoint._owned = set()
        finish = asyncio.Event()
        saved = False

        async def save() -> None:
            nonlocal saved
            await finish.wait()
            saved = True

        with pytest.raises(PlannerError):
            await endpoint._owned_work(save(), answer_within=0.001, protect_update=True)
        barrier.freeze("attempt")
        drained = asyncio.create_task(barrier.drain())
        await asyncio.sleep(0)
        assert not drained.done()
        with pytest.raises(PlannerError):
            await endpoint._owned_work(save(), protect_update=True)
        finish.set()
        await drained
        assert saved

    asyncio.run(run())
