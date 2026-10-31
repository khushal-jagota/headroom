"""Pure exact-intent validation against one captured destination observation."""

from __future__ import annotations

from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogObservation,
    CatalogUnavailable,
    CorrectiveAction,
    ModelSelection,
    ValidationReason,
    ValidationState,
)


def unresolved_validation(
    selection: ModelSelection,
    reason: ValidationReason,
    *,
    unavailable: CatalogUnavailable | None = None,
    observation: CatalogObservation | None = None,
) -> CanonicalModelValidation:
    return CanonicalModelValidation(
        selection=selection,
        state=ValidationState.unresolved,
        reason=reason,
        corrective_action=(
            unavailable.corrective_action if unavailable else CorrectiveAction.refresh_catalog
        ),
        launch_token=None,
    )
