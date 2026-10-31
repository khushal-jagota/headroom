"""Pure exact-intent validation against one captured destination observation."""

from __future__ import annotations

from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CorrectiveAction,
    ModelSelection,
    ValidationReason,
    ValidationState,
)


def unresolved_validation(
    selection: ModelSelection,
    reason: ValidationReason,
) -> CanonicalModelValidation:
    return CanonicalModelValidation(
        selection=selection,
        state=ValidationState.unresolved,
        reason=reason,
        corrective_action=CorrectiveAction.refresh_catalog,
        launch_token=None,
    )
