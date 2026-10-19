"""What the claude transport does when a steering command it admitted never closes out.

A steer's ending is a lifecycle receipt, and the turn that carried the steer holds its own
result until that receipt arrives. So the question every exercise here asks is the same
one: when does the transport stop waiting for a receipt, and when must it keep waiting?

The answer turns on one fact — whether claude has taken the command up as a turn of its
own. The receipts are the real ones the CLI emits, fed in as raw lines through the reader
the transport wraps.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from claude_agent_sdk import Transport

from planner.conversation.backends.claude_agent_sdk import _ClaudeProtocolTransport

STEER_UUID = "11111111-1111-4111-8111-111111111111"

# Short enough to keep these exercises quick, long enough that a scheduler hiccup does not
# read as a bound that fired.
A_SHORT_BOUND = 0.05


class _ScriptedInnerTransport(Transport):
    """A transport that emits exactly the lines a test gives it, in that order."""

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.written: list[str] = []

    async def connect(self) -> None:
        return None

    async def write(self, data: str) -> None:
        self.written.append(data)

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        return self._drain()

    async def _drain(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            message = await self._inbox.get()
            try:
                if message is None:
                    return
                yield message
            finally:
                self._inbox.task_done()

    async def close(self) -> None:
        return None

    def is_ready(self) -> bool:
        return True

    async def end_input(self) -> None:
        return None

    def say(self, message: dict[str, Any]) -> None:
        self._inbox.put_nowait(message)

    async def until_taken_in(self) -> None:
        await self._inbox.join()

    def end_the_stream(self) -> None:
        self._inbox.put_nowait(None)


def _lifecycle(state: str) -> dict[str, Any]:
    return {
        "type": "command_lifecycle",
        "command_uuid": STEER_UUID,
        "state": state,
        "uuid": "22222222-2222-4222-8222-222222222222",
        "session_id": "session-under-test",
    }


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 10.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


async def _reading(transport: _ClaudeProtocolTransport) -> asyncio.Task[None]:
    """Start the reader, which is what observes the receipts, and wait for it to run."""

    async def read() -> None:
        async for _ in transport.read_messages():
            pass

    reader = asyncio.create_task(read())
    await asyncio.sleep(0)
    return reader


async def _said(inner: _ScriptedInnerTransport, state: str) -> None:
    """Emit a receipt and return once the transport has observed it."""
    inner.say(_lifecycle(state))
    await inner.until_taken_in()
