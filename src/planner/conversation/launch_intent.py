"""Validate sender-authored selection kinds and bind saved model intent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from planner.conversation.model_resolution.contracts import SelectionKind

if TYPE_CHECKING:
    from planner.conversation.model_resolution.contracts import (
        CanonicalModelValidation,
        ModelSelection,
    )
    from planner.conversation.snapshot import BackendModel


def validate_launch_intent(model_change: str | None, selection_kind: SelectionKind | None) -> None:
    if selection_kind is not None:
        if (
            not isinstance(selection_kind, SelectionKind)
            or selection_kind is SelectionKind.legacy_unclassified
        ):
            raise ValueError("selection_kind must be an explicit selection kind")
        if model_change is None:
            raise ValueError("selection_kind requires an explicit model")


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
        launch_token=None if disabled else token,
    )
