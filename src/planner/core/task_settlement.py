"""Retain ownership of an asynchronous task until it settles."""

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi.concurrency import run_in_threadpool


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


async def run_on_worker_thread[Result](
    function: Callable[..., Result], /, *args: Any, **kwargs: Any
) -> Result:
    """Run blocking work on a worker thread, and keep owning it until the thread ends.

    This is the door for database work that must not run on the event loop. A thread
    cannot be stopped, so a cancelled caller waits for it to finish before the cancel
    goes through. Cleanup after the cancel, such as closing the connection the thread is
    using, then never runs under the thread.
    """
    task = asyncio.create_task(run_in_threadpool(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except BaseException:
        await wait_for_settlement(task)
        raise
