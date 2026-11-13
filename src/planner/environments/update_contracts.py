"""The publication identity, the one update attempt, and the owner update status.

The attempt record is the one value that crosses application versions: the server
that writes it may be older or newer than the installer or server that reads it.
AppManifest remains the authority for the installed application's identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from planner.environments.app import AppManifest

SUPPORTED_UPDATE_SCHEMA_FLOOR = "video_upload_ledger"
UPDATE_ARTIFACT_RETENTION_DAYS = 30

type UpdateMode = Literal["now", "idle"]
type UpdateActivity = Literal["idle", "busy", "unknown"]
type UpdateAttemptState = Literal["waiting", "preparing", "installing", "done", "failed"]
type UpdateExperienceState = Literal[
    "up_to_date", "available", "waiting", "updating", "failed", "mac_failed", "mac_unreachable"
]
type UpdateAction = Literal["update", "cancel", "update_now", "retry", "retry_mac"]


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
class UpdateAttempt:
    """One owner press, from waiting to its end, in `<data>/update-attempt.json`.

    `steps` maps a step name to the UTC time it finished; it is the timing evidence.
    `extra` holds fields this version does not know, so a write preserves them.
    """

    attempt_id: str
    from_sha: str
    to_sha: str
    mode: UpdateMode
    confirm_activity: bool
    state: UpdateAttemptState
    created_at: str
    updated_at: str
    release: PublishedUpdate
    detail: str | None = None
    deadline_at: str | None = None
    backup: str | None = None
    steps: dict[str, str] = field(default_factory=dict)
    mac_attempt_id: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    """The `GET /api/updates` body, apart from the Mac details the route adds."""

    state: UpdateExperienceState
    allowed_actions: tuple[UpdateAction, ...]
    activity: UpdateActivity
    available: PublishedUpdate | None
    attempt: UpdateAttempt | None
    app_sha: str | None
    detail: str | None
    deadline_at: str | None


class UpdateProvider(Protocol):
    """Repository-scoped external reads; credentials never enter a record."""

    def available(self) -> PublishedUpdate | None: ...

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        """Stream one artifact ZIP to `destination` on disk, never into memory."""
        ...
