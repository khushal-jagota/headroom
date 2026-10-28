"""Persistent lifecycle state; metadata contracts remain independently owned."""

from dataclasses import dataclass
from typing import NotRequired, TypedDict

from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogWorkspaceBinding,
    CorrectiveAction,
    ModelSelection,
    ValidationReason,
    ValidationState,
)


@dataclass(frozen=True, slots=True)
class StoredLaunchValidation:
    validation: CanonicalModelValidation
    workspace: CatalogWorkspaceBinding
    launch_revision: int
    transition_revision: int
    notification_fingerprint: str
    checked_at: int
    current: bool


class LaunchValidationProjection(TypedDict):
    """JSON projection; nested canonical contracts retain their shared identities."""

    selection: ModelSelection | None
    validation: CanonicalModelValidation | None
    current: bool
    transition_revision: int | None
    state: NotRequired[ValidationState]
    reason: NotRequired[ValidationReason]
    corrective_action: NotRequired[CorrectiveAction]
