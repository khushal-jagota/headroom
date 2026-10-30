"""Own a metadata probe until its physical resources settle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import anyio

from planner.core.task_settlement import wait_for_settlement


async def owned_probe[Result](
    operation: Callable[[], Awaitable[Result]], *, timeout_seconds: float
) -> Result:
    """Cancel through AnyIO so SDK cleanup shields retain their semantics.

    The caller never cancels the owning asyncio task. Repeated caller cancellation
    cannot interrupt the SDK disconnect or release an outer capacity reservation.
    """
    scopes: list[anyio.CancelScope] = []
    ready = asyncio.Event()

    async def own() -> Result:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            ready.set()
            result = await operation()
            return result
        raise asyncio.CancelledError

    task = asyncio.create_task(own(), name="provider-metadata-probe")
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=max(0, timeout_seconds))
    except BaseException:
        # A zero deadline can expire before the owning task enters its scope.
        with anyio.CancelScope(shield=True):
            ready_task = asyncio.create_task(ready.wait())
            await wait_for_settlement(ready_task)
            scopes[0].cancel()
            await wait_for_settlement(task)
        raise
