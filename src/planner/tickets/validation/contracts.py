"""Persistent lifecycle state; metadata contracts remain independently owned."""

from dataclasses import dataclass

from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CatalogWorkspaceBinding,
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
