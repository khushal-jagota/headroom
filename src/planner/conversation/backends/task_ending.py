"""Ending a background task for good, whatever state it is in."""

from __future__ import annotations

import asyncio
import logging


async def end_task(task: asyncio.Task[None], *, what: str, logger: logging.Logger) -> None:
    """Cancel a task and wait for it. A task that already died with an error is ended too.

    Awaiting a dead task raises the error it died with, every time. A stop that let that
    escape never reached the process, and a retry hit the same dead task again. The error
    is logged here, once, and the stop goes on.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.warning("%s had already ended with an error", what, exc_info=True)
