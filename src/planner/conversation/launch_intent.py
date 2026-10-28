"""Sender-authored nullable effort presence, retained across asynchronous settlement."""

from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from functools import wraps
from typing import Any

from planner.conversation.model_resolution.contracts import SelectionKind

_selection_kind: ContextVar[SelectionKind | None] = ContextVar("selection_kind", default=None)


def selection_kind() -> SelectionKind | None:
    return _selection_kind.get()


_effort_is_set: ContextVar[bool] = ContextVar("reasoning_effort_is_set", default=False)


def reasoning_effort_is_set() -> bool:
    return _effort_is_set.get()


def effort_presence[**P, T](
    function: Callable[P, Awaitable[T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        kind = kwargs.get("selection_kind")
        if kind is not None:
            if not isinstance(kind, SelectionKind) or kind is SelectionKind.legacy_unclassified:
                raise ValueError("selection_kind must be an explicit selection kind")
            if kwargs.get("model_change") is None:
                raise ValueError("selection_kind requires an explicit model")
        kind_token = _selection_kind.set(kind)
        token = _effort_is_set.set(bool(kwargs.get("reasoning_effort_is_set", False)))
        try:
            return await function(*args, **kwargs)
        finally:
            _effort_is_set.reset(token)
            _selection_kind.reset(kind_token)

    return wrapped
