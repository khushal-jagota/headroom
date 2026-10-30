"""Durable pinned requests outside the database and its snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

from planner.core import change_signal
from planner.environments.app import AppManifest
from planner.environments.deployment_lifecycle import _atomic_write, _exclusive_lock
from planner.environments.runtime_contracts import parse_optional_runtime_contract
from planner.environments.update_contracts import (
    OwnerUpdateRequest,
    PublishedUpdate,
    UpdateMode,
    UpdateRequestState,
)


class UpdateConflict(ValueError):
    """The recorded request no longer permits this action."""


def parse_release(payload: dict[str, object]) -> PublishedUpdate:
    manifest = payload["manifest"]
    if not isinstance(manifest, dict):
        raise ValueError("publication manifest must be an object")
    return PublishedUpdate(
        publication_run_id=int(str(payload["publication_run_id"])),
        artifact_id=int(str(payload["artifact_id"])),
        proof_artifact_id=int(str(payload["proof_artifact_id"])),
        manifest=AppManifest(
            app_sha=manifest["app_sha"],
            source_digest=manifest["source_digest"],
            artifact_digest=manifest["artifact_digest"],
            runtime_contract=parse_optional_runtime_contract(manifest.get("runtime_contract")),
        ),
        archive_sha256=str(payload["archive_sha256"]),
        schema_floor=str(payload["schema_floor"]),
        expires_at=str(payload["expires_at"]),
    )


class FileUpdateRequestStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")

    def read(self) -> OwnerUpdateRequest | None:
        try:
            payload = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        return OwnerUpdateRequest(
            request_id=payload["request_id"],
            release=parse_release(payload["release"]),
            mode=cast(UpdateMode, payload["mode"]),
            state=cast(UpdateRequestState, payload["state"]),
            created_at=payload["created_at"],
            detail=payload.get("detail"),
            confirm_activity=payload.get("confirm_activity", False),
        )

    def create(self, request: OwnerUpdateRequest) -> OwnerUpdateRequest:
        with _exclusive_lock(self.lock_path):
            current = self.read()
            if current is not None and current.state in {"pending", "dispatched", "applying"}:
                raise UpdateConflict("An update request already exists.")
            _atomic_write(self.path, asdict(request))
            change_signal.emit()
            return request

    def transition(
        self,
        request_id: str,
        expected: frozenset[UpdateRequestState],
        state: UpdateRequestState,
        *,
        detail: str | None = None,
    ) -> OwnerUpdateRequest:
        with _exclusive_lock(self.lock_path):
            current = self.read()
            if current is None or current.request_id != request_id or current.state not in expected:
                raise UpdateConflict("The update request changed or application already began.")
            updated = replace(current, state=state, detail=detail)
            _atomic_write(self.path, asdict(updated))
            change_signal.emit()
            return updated
