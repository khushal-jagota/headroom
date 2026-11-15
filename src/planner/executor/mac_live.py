"""Live text and progress, sent as it happens and dropped when it cannot be.

Live events are a view, never the record: the finished message arrives through the
outbox. Each conversation has at most one request in flight. Events that arrive while
it is in flight wait, at most MAX_LIVE_EVENTS_PER_BATCH of them (the oldest go first),
and leave together in the next request. A failed request is dropped, not retried.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from typing import TYPE_CHECKING

from planner.executor.configuration import MAX_ENVELOPE_BYTES, MAX_LIVE_EVENTS_PER_BATCH
from planner.executor.protocol import BackendEvent, LiveEventBatch, type_adapter

if TYPE_CHECKING:
    from planner.executor.contracts import MacHttpClient


class MacLiveSender:
    def __init__(self, client: MacHttpClient) -> None:
        self.client = client
        self._waiting: dict[str, deque[BackendEvent]] = {}
        self._senders: dict[str, asyncio.Task[None]] = {}

    def send(self, event: BackendEvent) -> None:
        conversation_id = event.child.conversation_id
        waiting = self._waiting.setdefault(conversation_id, deque(maxlen=MAX_LIVE_EVENTS_PER_BATCH))
        waiting.append(event)
        if conversation_id not in self._senders:
            self._senders[conversation_id] = asyncio.create_task(self._send(conversation_id))

    def _next_batch(self, waiting: deque[BackendEvent]) -> LiveEventBatch:
        events: list[BackendEvent] = []
        size = len(b'{"events":[]}')
        while waiting:
            event_size = len(type_adapter(BackendEvent).dump_json(waiting[0])) + 1
            if size + event_size > MAX_ENVELOPE_BYTES:
                if not events:
                    waiting.popleft()  # One event too large to send at all.
                    continue
                break
            events.append(waiting.popleft())
            size += event_size
        return LiveEventBatch(tuple(events))

    async def _send(self, conversation_id: str) -> None:
        waiting = self._waiting[conversation_id]
        try:
            while waiting:
                batch = self._next_batch(waiting)
                if batch.events:
                    with contextlib.suppress(Exception):
                        await self.client.post_live(conversation_id, batch)
        finally:
            del self._senders[conversation_id]
            if not waiting:
                self._waiting.pop(conversation_id, None)

    async def close(self) -> None:
        senders = tuple(self._senders.values())
        for sender in senders:
            sender.cancel()
        await asyncio.gather(*senders, return_exceptions=True)
