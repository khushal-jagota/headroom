"""The publication identity and one owner-authorized adoption request.

These values cross the browser, publication workflow, and deployment runner.
AppManifest remains the authority for the installed application's identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from planner.environments.app import AppManifest

SUPPORTED_UPDATE_SCHEMA_FLOOR = "video_upload_ledger"
UPDATE_ARTIFACT_RETENTION_DAYS = 30

type UpdateMode = Literal["now", "idle"]
type UpdateActivity = Literal["idle", "busy", "unknown"]
type UpdateRequestState = Literal[
    "pending", "dispatched", "applying", "applied", "cancelled", "failed"
]


@dataclass(frozen=True, slots=True)
class PublishedUpdate:
    publication_run_id: int
    artifact_id: int
    proof_artifact_id: int
    manifest: AppManifest
    archive_sha256: str
    schema_floor: str
    expires_at: str


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


class UpdateProvider(Protocol):
    """Repository-scoped external operations; credentials never enter a receipt."""

    def available(self) -> PublishedUpdate | None: ...

    def dispatch(self, request: OwnerUpdateRequest) -> None: ...

    def download_artifact(self, artifact_id: int, destination: Path) -> None: ...


class UpdateRequestStore(Protocol):
    """One request outside the application database's rollback scope."""

    def read(self) -> OwnerUpdateRequest | None: ...

    def create(self, request: OwnerUpdateRequest) -> OwnerUpdateRequest: ...

    def transition(
        self,
        request_id: str,
        expected: frozenset[UpdateRequestState],
        state: UpdateRequestState,
        *,
        detail: str | None = None,
    ) -> OwnerUpdateRequest: ...
