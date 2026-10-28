"""Classify new requested intent only from documented policy or affirmative facts."""

from dataclasses import replace

from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogObservation,
    ModelSelection,
    SelectionKind,
)
from planner.conversation.model_resolution.logic.variants import recognize_variant


def classify_new_selection(
    selection: ModelSelection, observation: CatalogObservation | None = None
) -> ModelSelection:
    if selection.selection_kind is not SelectionKind.legacy_unclassified:
        return selection
    variant = recognize_variant(selection.backend_key, selection.submitted_token)
    if variant is not None and variant.name == selection.submitted_token:
        return replace(selection, selection_kind=SelectionKind.panels_alias)
    if observation is None:
        return selection
    kinds = {
        row.selection_kind
        for row in observation.rows
        if not row.hidden and row.submitted_token == selection.submitted_token
    }
    if len(kinds) == 1 and SelectionKind.legacy_unclassified not in kinds:
        return replace(selection, selection_kind=next(iter(kinds)))
    return selection


def preserve_alias_history(
    validation: CanonicalModelValidation, prior: CanonicalModelValidation | None,
) -> CanonicalModelValidation:
    """Repeated checks retain the last distinct target and its original evidence."""
    if prior is None or validation.current_alias_resolution is None or prior.current_alias_resolution is None:
        return validation
    if (
        validation.selection.submitted_token == prior.selection.submitted_token
        and validation.selection.selection_kind == prior.selection.selection_kind
        and validation.current_alias_resolution.resolved_model_id
        == prior.current_alias_resolution.resolved_model_id
    ):
        return replace(validation, previous_alias_resolution=prior.previous_alias_resolution)
    return validation
