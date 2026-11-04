"""Admission fails immediately; cancellation does not leak the shared pacer lock."""

from __future__ import annotations

import asyncio

import pytest

from planner.previews.contracts import PreviewError
from planner.previews.limits import Capacity, TransferBudget


def test_capacity_accounts_for_both_generation_and_global_limit() -> None:
    capacity = Capacity(global_limit=3, per_preview=2)
    capacity.acquire("one")
    capacity.acquire("one")
    with pytest.raises(PreviewError, match="busy"):
        capacity.acquire("one")
    capacity.acquire("two")
    with pytest.raises(PreviewError, match="busy"):
        capacity.acquire("three")
    capacity.release("one")
    capacity.acquire("three")
    capacity.release("one")
    assert "one" not in capacity.active


def test_transfer_pacer_cancellation_does_not_block_other_connections() -> None:
    async def run() -> None:
        budget = TransferBudget(rate=1, burst=1)
        await budget.consume(1)
        waiter = asyncio.create_task(budget.consume(1))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not budget.lock.locked()
        budget.available = 1
        async with asyncio.timeout(0.1):
            await budget.consume(1)

    asyncio.run(run())


def test_unknown_generations_and_revoked_tasks_do_not_accumulate_tracking_entries() -> None:
    from unittest.mock import AsyncMock, create_autospec

    from starlette.websockets import WebSocket

    from planner.previews.service import PreviewService
    from planner.previews.transport import PreviewTransport

    async def run() -> None:
        service = create_autospec(PreviewService, instance=True)
        service.check = AsyncMock(side_effect=PreviewError("Preview ended.", 410))
        transport = PreviewTransport(service)
        for index in range(100):
            websocket = WebSocket({"type": "websocket"}, AsyncMock(), AsyncMock())
            await transport.websocket(websocket, "t_unknown", f"unknown-{index}")
        assert not transport.tasks
        assert not transport.sessions
        assert not transport.websocket_capacity.active
        task = asyncio.current_task()
        assert task is not None
        # A lifecycle revoke removes the mapping before stream cleanup executes.
        transport.untrack("already-revoked", task)
        assert not transport.tasks
        await transport.close()

    asyncio.run(run())
