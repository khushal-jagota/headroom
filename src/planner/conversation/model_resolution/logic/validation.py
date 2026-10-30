"""Pure exact-intent validation against one captured destination observation."""

from __future__ import annotations

from planner.conversation.model_resolution.contracts import (
    AliasResolutionFact,
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
    previous_alias_resolution: AliasResolutionFact | None = None,
) -> CanonicalModelValidation:
    return CanonicalModelValidation(
        selection=selection,
        state=ValidationState.unresolved,
        reason=reason,
        corrective_action=(
            unavailable.corrective_action if unavailable else CorrectiveAction.refresh_catalog
        ),
        resolved_model_id=None,
        launch_token=None,
        evidence=observation.evidence if observation else None,
        current_alias_resolution=None,
        previous_alias_resolution=previous_alias_resolution,
        unavailable=unavailable,
    )
