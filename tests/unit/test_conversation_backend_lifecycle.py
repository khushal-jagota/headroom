"""The race boundary between backend children and installation maintenance."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from planner.conversation.backend_lifecycle import BackendLifecycleCoordinator
from planner.conversation.contracts import ConversationBackendKey


def _run(exercise: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(asyncio.wait_for(exercise(), 20.0))


def test_a_child_start_reservation_refuses_maintenance() -> None:
    async def exercise() -> None:
        lifecycle = BackendLifecycleCoordinator()
        release_start = asyncio.Event()
        start_reserved = asyncio.Event()

        async def start_child() -> None:
            async with lifecycle.child_start(ConversationBackendKey.hermes):
                start_reserved.set()
                await release_start.wait()

        starting = asyncio.create_task(start_child())
        await start_reserved.wait()

        assert (
            await lifecycle.try_begin_maintenance(ConversationBackendKey.hermes) is None
        )

        release_start.set()
        await starting
        await lifecycle.child_stopped(ConversationBackendKey.hermes)

    _run(exercise)


def test_an_accepted_update_excludes_a_new_child_until_refresh_is_done() -> None:
    async def exercise() -> None:
        lifecycle = BackendLifecycleCoordinator()
        lease = await lifecycle.try_begin_maintenance(ConversationBackendKey.hermes)
        assert lease is not None
        child_entered = asyncio.Event()

        async def start_child() -> None:
            async with lifecycle.child_start(ConversationBackendKey.hermes):
                child_entered.set()

        starting = asyncio.create_task(start_child())
        for _ in range(20):
            await asyncio.sleep(0)
        assert not child_entered.is_set()

        await lifecycle.end_maintenance(lease)
        await starting
        assert child_entered.is_set()
        await lifecycle.child_stopped(ConversationBackendKey.hermes)

    _run(exercise)
