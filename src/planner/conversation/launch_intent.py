"""Sender-authored nullable effort presence, retained across asynchronous settlement."""

from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from functools import wraps
from typing import Any

_effort_is_set: ContextVar[bool] = ContextVar("reasoning_effort_is_set", default=False)


def reasoning_effort_is_set() -> bool:
    return _effort_is_set.get()


def effort_presence[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        token = _effort_is_set.set(bool(kwargs.get("reasoning_effort_is_set", False)))
        try:
            return await function(*args, **kwargs)
        finally:
            _effort_is_set.reset(token)

    return wrapped
