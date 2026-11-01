"""Retain ownership of an asynchronous task until it settles."""

import asyncio
from typing import Any


async def wait_for_settlement(task: asyncio.Task[Any]) -> None:
    """Repeated caller cancellation cannot cancel the owned physical operation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    if task.done() and not task.cancelled():
        task.exception()
