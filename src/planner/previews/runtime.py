"""Preview lifetime follows canonical Ticket state and the server process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from planner.core import change_signal
from planner.previews.config import LIFECYCLE_SECONDS
from planner.previews.contracts import PreviewError
from planner.previews.service import PreviewService
from planner.previews.transport import PreviewTransport

_log = logging.getLogger(__name__)


class PreviewRuntime:
    def __init__(self, service: PreviewService, transport: PreviewTransport) -> None:
        self.service = service
        self.transport = transport
        self._task: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] | None = None

    async def start(self) -> None:
        await self.service.start()
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()

        def changed() -> None:
            loop.call_soon_threadsafe(wake.set)

        self._unsubscribe = change_signal.subscribe(changed)

        async def monitor() -> None:
            while True:
                wake.clear()
                try:
                    for generation in await self.service.recheck_all():
                        await self.transport.revoke(generation)
                except PreviewError:
                    _log.exception("Preview lifecycle inspection refused; retrying")
                try:
                    await asyncio.wait_for(wake.wait(), LIFECYCLE_SECONDS)
                except TimeoutError:
                    pass

        self._task = asyncio.create_task(monitor(), name="application-preview-lifecycle")

    async def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        try:
            await self.transport.close()
        finally:
            await self.service.close()
