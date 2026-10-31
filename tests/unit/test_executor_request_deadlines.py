"""Shared Link deadlines and the domain's separate catalog failure authority."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from tests.support.executor_control import MessageStream, deliver, message_link

from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.protocol import WireMessage, WireMessageKind

IDENTITY = ExecutorIdentity(
    PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "12345678-1234-5678-1234-567812345678", 1
)


async def stalled_send(*_: object) -> None:
    await asyncio.Event().wait()


def peer() -> Any:
    writer: Any = MessageStream()
    writer.send = AsyncMock()
    writer.close = Mock(wraps=writer.close)
    writer.wait_closed = AsyncMock(wraps=writer.wait_closed)
    return writer


@pytest.mark.parametrize("blocked", ["response", "drain", "lock"])
def test_absolute_request_deadline_covers_send_and_response(blocked: str) -> None:
    async def run() -> None:
        writer = peer()
        link = message_link(writer, IDENTITY)
        if blocked == "drain":
            writer.send.side_effect = stalled_send
        if blocked == "lock":
            await link._lock.acquire()
        try:
            with pytest.raises(TimeoutError):
                await link.request(WireMessageKind.envelope, {}, timeout=0.02)
            assert not link._pending
            writer.close.assert_called_once_with()
            assert writer.send.call_count == (0 if blocked == "lock" else 1)
        finally:
            if blocked == "lock":
                link._lock.release()
            cast_reader = link._stream
            assert isinstance(cast_reader, MessageStream)
            cast_reader.feed_eof()
            await link.wait_closed()

    asyncio.run(run())


def test_late_response_after_cancellation_is_ignored() -> None:
    async def run() -> None:
        writer = peer()
        link = message_link(writer, IDENTITY)
        task = asyncio.create_task(link.request(WireMessageKind.envelope, {}))
        while not writer.send.called:
            await asyncio.sleep(0)
        request_id = next(iter(link._pending))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await deliver(link, WireMessage(WireMessageKind.receipt, request_id, {}))
        assert not link._pending and not link.is_closing()

    asyncio.run(run())
