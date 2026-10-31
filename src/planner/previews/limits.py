"""Immediate capacity admission and cooperative bounded transfer pacing."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Callable

from planner.previews import config
from planner.previews.contracts import PreviewError


class Capacity:
    def __init__(self, global_limit: int, per_preview: int) -> None:
        self.global_limit = global_limit
        self.per_preview = per_preview
        self.active: Counter[str] = Counter()

    def acquire(self, generation: str) -> None:
        if (
            sum(self.active.values()) >= self.global_limit
            or self.active[generation] >= self.per_preview
        ):
            raise PreviewError("Preview is busy. Try again shortly.", 503)
        self.active[generation] += 1

    def release(self, generation: str) -> None:
        if self.active[generation] <= 1:
            self.active.pop(generation, None)
        else:
            self.active[generation] -= 1


class TransferBudget:
    def __init__(
        self,
        rate: int = config.TRANSFER_BYTES_PER_SECOND,
        burst: int = config.TRANSFER_BURST_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate
        self.burst = burst
        self.clock = clock
        self.available = float(burst)
        self.updated = clock()
        self.lock = asyncio.Lock()

    async def consume(self, amount: int) -> None:
        while amount:
            part = min(amount, self.burst)
            async with self.lock:
                now = self.clock()
                self.available = min(self.burst, self.available + (now - self.updated) * self.rate)
                self.updated = now
                if self.available >= part:
                    self.available -= part
                    amount -= part
                    delay = 0.0
                else:
                    delay = (part - self.available) / self.rate
            await asyncio.sleep(delay)


class MessageBudget:
    def __init__(self) -> None:
        self.tokens = float(config.WEBSOCKET_MESSAGE_BURST)
        self.updated = time.monotonic()

    def consume(self) -> None:
        now = time.monotonic()
        self.tokens = min(
            config.WEBSOCKET_MESSAGE_BURST,
            self.tokens + (now - self.updated) * config.WEBSOCKET_MESSAGES_PER_SECOND,
        )
        self.updated = now
        if self.tokens < 1:
            raise PreviewError("Preview WebSocket message rate exceeded.", 503)
        self.tokens -= 1
