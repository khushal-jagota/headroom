"""Keep a synchronous conversation test and its fixture cleanup on one loop."""

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_runner: ContextVar[asyncio.Runner | None] = ContextVar("conversation_test_runner", default=None)


@contextmanager
def conversation_test_loop() -> Iterator[asyncio.Runner]:
    with asyncio.Runner() as runner:
        token = _runner.set(runner)
        try:
            yield runner
        finally:
            _runner.reset(token)


def run_conversation_test(exercise: Callable[[], Coroutine[Any, Any, None]]) -> None:
    async def bounded() -> None:
        await asyncio.wait_for(exercise(), 20.0)

    runner = _runner.get()
    if runner is None:
        asyncio.run(bounded())
    else:
        runner.run(bounded())
