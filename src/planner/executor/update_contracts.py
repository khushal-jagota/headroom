"""Version-independent Mac update channel, separate from the work protocol.

Targets come only from the owner's update attempt. A work-protocol refusal
does not prevent an authenticated executor from obtaining its recovery target.
Mac runtimes in the field read this shape: add only optional fields, which they ignore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from planner.environments.update_contracts import UpdateMode
from planner.execution_identity import ExecutorIdentity
from planner.executor.update_configuration import (
    MAC_UPDATE_SCHEMA_VERSION as MAC_UPDATE_SCHEMA_VERSION,
)

type MacUpdatePhase = Literal[
    "waiting", "downloading", "ready_to_restart", "current", "failed", "reverted"
]


@dataclass(frozen=True, slots=True)
class MacUpdateTarget:
    request_id: str
    application_revision: str
    mode: UpdateMode
    activate: bool
    archive_sha256: str
    archive_size: int
    archive_path: str


def mac_prefetch_archive_path(archive_sha256: str) -> str:
    return f"/api/executor/update/prefetch/{archive_sha256}"


@dataclass(frozen=True, slots=True)
class MacUpdatePrefetch:
    """The ready release's runtime, which a Mac may download before any update starts."""

    application_revision: str
    archive_sha256: str
    archive_size: int
    archive_path: str


@dataclass(frozen=True, slots=True)
class MacUpdateCheck:
    schema_version: int
    target: MacUpdateTarget | None
    prefetch: MacUpdatePrefetch | None = None


@dataclass(frozen=True, slots=True)
class MacUpdateReport:
    request_id: str
    application_revision: str
    phase: MacUpdatePhase
    detail: str | None = None
    identity: ExecutorIdentity | None = None


@dataclass(frozen=True, slots=True)
class MacUpdateStatus:
    request_id: str
    target_revision: str
    phase: MacUpdatePhase | Literal["unavailable"]
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class MacRuntimeManifest:
    schema_version: int
    application_revision: str
    platform: str
    architecture: str


@dataclass(frozen=True, slots=True)
class MacRuntimeSelection:
    request_id: str
    application_revision: str
    runtime_path: str
    previous_runtime_path: str | None
    previous_revision: str
    executor_incarnation: str
    connection_generation: int
    archive_sha256: str
