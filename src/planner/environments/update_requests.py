"""Durable pinned requests outside the database and its snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

from planner.core import change_signal
from planner.environments.app import AppManifest
from planner.environments.deployment_lifecycle import _atomic_write, _exclusive_lock
from planner.environments.runtime_contracts import parse_optional_runtime_contract
from planner.environments.update_configuration import MAX_CANCELLED_UPDATE_REPORTS
from planner.environments.update_contracts import (
    OwnerUpdateRequest,
    PublishedMacUpdate,
    PublishedUpdate,
    UpdateMode,
    UpdateRequestState,
)

CANCELLED_DETAIL = "Update canceled before installation. The previous version stayed active."


class UpdateConflict(ValueError):
    """The recorded request no longer permits this action."""


def parse_release(payload: dict[str, object]) -> PublishedUpdate:
    manifest = payload["manifest"]
    if not isinstance(manifest, dict):
        raise ValueError("publication manifest must be an object")
    mac = payload.get("mac")
    if mac is not None and not isinstance(mac, dict):
        raise ValueError("Mac publication must be an object")
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
        mac=None if mac is None else PublishedMacUpdate(
            artifact_id=mac["artifact_id"], archive_sha256=mac["archive_sha256"],
            archive_size=mac["archive_size"],
        ),
    )


class FileUpdateRequestStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.name + ".lock")

    def read(self) -> OwnerUpdateRequest | None:
        return self._read_path(self.path)

    @staticmethod
    def _read_path(path: Path) -> OwnerUpdateRequest | None:
        try:
            payload = json.loads(path.read_text())
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

    def _cancelled_path(self, request_id: str) -> Path:
        # Hash the identity so a malformed request cannot escape the report directory.
        name = hashlib.sha256(request_id.encode()).hexdigest()
        return self.path.parent / "cancelled-update-requests" / f"{name}.json"

    def _prune_cancelled_reports(self) -> None:
        directory = self.path.parent / "cancelled-update-requests"
        reports = [
            path
            for path in directory.glob("*.json")
            if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) and not path.is_symlink()
        ]
        reports.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
        for path in reports[MAX_CANCELLED_UPDATE_REPORTS - 1 :]:
            path.unlink()

    def read_for_reporting(self, request_id: str) -> OwnerUpdateRequest | None:
        """Read evidence only. Archived requests never authorize an update action."""
        try:
            current = self.read()
            if current is not None and current.request_id == request_id:
                return current
            archived = self._read_path(self._cancelled_path(request_id))
            if (
                archived is not None
                and archived.request_id == request_id
                and archived.state == "failed"
                and archived.detail == CANCELLED_DETAIL
            ):
                return archived
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def create(self, request: OwnerUpdateRequest) -> OwnerUpdateRequest:
        with _exclusive_lock(self.lock_path):
            current = self.read()
            if current is not None and current.state in {"requested", "installing"}:
                raise UpdateConflict("An update request already exists.")
            if (
                current is not None
                and current.state == "failed"
                and current.detail == CANCELLED_DETAIL
            ):
                try:
                    self._prune_cancelled_reports()
                    _atomic_write(self._cancelled_path(current.request_id), asdict(current))
                except (OSError, ValueError, TypeError):
                    # Missing reporting evidence cannot block replacement.
                    pass
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

    def promote(self, request_id: str, *, confirm_activity: bool) -> OwnerUpdateRequest:
        if not confirm_activity:
            raise UpdateConflict("Confirm active work before Update now.")
        with _exclusive_lock(self.lock_path):
            current = self.read()
            if (current is None or current.request_id != request_id
                    or current.state != "requested" or current.mode != "idle"):
                raise UpdateConflict("The waiting update changed or already began.")
            updated = replace(current, mode="now", confirm_activity=True)
            _atomic_write(self.path, asdict(updated))
            change_signal.emit()
            return updated
