"""The publication identity and one owner-authorized adoption request.

These values cross the browser, publication workflow, and deployment runner.
AppManifest remains the authority for the installed application's identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from planner.environments.app import AppManifest
from planner.environments.deployment_lifecycle import DeploymentPhase

SUPPORTED_UPDATE_SCHEMA_FLOOR = "video_upload_ledger"
UPDATE_ARTIFACT_RETENTION_DAYS = 30

type UpdateMode = Literal["now", "idle"]
type UpdateActivity = Literal["idle", "busy", "unknown"]
type UpdateExperienceState = Literal[
    "up_to_date", "available", "waiting", "updating", "restarting", "failed", "complete"
]
type UpdateAction = Literal["update", "cancel", "update_now", "retry"]
type UpdateRequestState = Literal[
    "requested", "installing", "done", "failed"
]


@dataclass(frozen=True, slots=True)
class PublishedMacUpdate:
    """The prebuilt arm64 runtime from the same source as the VPS publication."""

    artifact_id: int
    archive_sha256: str
    archive_size: int


@dataclass(frozen=True, slots=True)
class PublishedUpdate:
    publication_run_id: int
    artifact_id: int
    proof_artifact_id: int
    manifest: AppManifest
    archive_sha256: str
    schema_floor: str
    expires_at: str
    mac: PublishedMacUpdate | None = None


@dataclass(frozen=True, slots=True)
class OwnerUpdateRequest:
    request_id: str
    release: PublishedUpdate
    mode: UpdateMode
    state: UpdateRequestState
    created_at: str
    detail: str | None = None
    confirm_activity: bool = False


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    configured: bool
    available: PublishedUpdate | None
    request: OwnerUpdateRequest | None
    activity: UpdateActivity
    detail: str | None = None
    state: UpdateExperienceState = "up_to_date"
    allowed_actions: tuple[UpdateAction, ...] = ()
    phase: DeploymentPhase | None = None
    app_sha: str | None = None


class UpdateProvider(Protocol):
    """Repository-scoped external operations; credentials never enter a receipt."""

    def available(self) -> PublishedUpdate | None: ...

    def dispatch(self, request: OwnerUpdateRequest) -> None: ...

    def download_artifact(self, artifact_id: int, destination: Path) -> None: ...


class UpdateRequestStore(Protocol):
    """One request outside the application database's rollback scope."""

    def read(self) -> OwnerUpdateRequest | None: ...

    def create(self, request: OwnerUpdateRequest) -> OwnerUpdateRequest: ...

    def promote(self, request_id: str, *, confirm_activity: bool) -> OwnerUpdateRequest: ...

    def transition(
        self,
        request_id: str,
        expected: frozenset[UpdateRequestState],
        state: UpdateRequestState,
        *,
        detail: str | None = None,
    ) -> OwnerUpdateRequest: ...
