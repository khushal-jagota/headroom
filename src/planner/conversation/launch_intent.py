"""Sender-authored nullable effort presence, retained across asynchronous settlement."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, Any

from planner.conversation.model_resolution.contracts import SelectionKind

if TYPE_CHECKING:
    from planner.conversation.model_resolution.contracts import (
        CanonicalModelValidation,
        ModelSelection,
    )
    from planner.conversation.snapshot import BackendModel


_selection_kind: ContextVar[SelectionKind | None] = ContextVar("selection_kind", default=None)


def selection_kind() -> SelectionKind | None:
    return _selection_kind.get()


_effort_is_set: ContextVar[bool] = ContextVar("reasoning_effort_is_set", default=False)


def reasoning_effort_is_set() -> bool:
    return _effort_is_set.get()


@contextmanager
def carried_effort_presence(is_set: bool) -> Iterator[None]:
    """Restore captured nullable intent when a queued or native command executes."""
    token = _effort_is_set.set(is_set)
    try:
        yield
    finally:
        _effort_is_set.reset(token)


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


def bind_model_intent(
    selection: ModelSelection,
    *,
    models: tuple[BackendModel, ...] = (),
    disabled_models: frozenset[str] = frozenset(),
) -> CanonicalModelValidation:
    """Bind exact intent locally. Only saved Panels aliases need retained suggestions."""
    from planner.conversation.backends.claude_model_catalog import claude_launch_model
    from planner.conversation.contracts import ConversationBackendKey
    from planner.conversation.model_resolution.contracts import (
        CanonicalModelValidation,
        CorrectiveAction,
        ValidationReason,
        ValidationState,
    )
    from planner.conversation.model_resolution.logic.variants import (
        numerical_version,
        recognize_variant,
    )

    token: str | None = selection.submitted_token
    native_alias = selection.backend_key is ConversationBackendKey.claude and (
        selection.submitted_token.removesuffix("[1m]") in {"opus", "sonnet", "haiku"}
    )
    if selection.selection_kind is SelectionKind.panels_alias and not native_alias:
        family = recognize_variant(selection.backend_key, selection.submitted_token)
        candidates = [
            model for model in models
            if family is not None
            and recognize_variant(selection.backend_key, model.model_id) == family
            and model.model_id not in disabled_models
        ]
        token = (
            max(candidates, key=lambda model: numerical_version(model.model_id)).model_id
            if candidates else None
        )
    if selection.backend_key is ConversationBackendKey.claude:
        token = claude_launch_model(token)
    disabled = selection.submitted_token in disabled_models or token in disabled_models
    reason = (
        ValidationReason.model_incompatible if disabled
        else ValidationReason.alias_mapping_unavailable if token is None
        else ValidationReason.supported
    )
    return CanonicalModelValidation(
        selection=selection,
        state=ValidationState.incompatible if disabled else ValidationState.unresolved
        if token is None else ValidationState.compatible,
        reason=reason,
        corrective_action=CorrectiveAction.choose_model if disabled or token is None
        else CorrectiveAction.none,
        resolved_model_id=None,
        launch_token=None if disabled else token,
        evidence=None,
        current_alias_resolution=None,
        previous_alias_resolution=None,
        unavailable=None,
    )
