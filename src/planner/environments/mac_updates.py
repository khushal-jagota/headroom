"""Retain the approved Mac runtime independently of publication expiry and VPS rollback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import zipfile
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from planner.core import change_signal
from planner.environments.deployment_lifecycle import _atomic_write
from planner.environments.update_contracts import (
    OwnerUpdateRequest,
    PublishedUpdate,
    UpdateProvider,
    UpdateRequestStore,
)
from planner.environments.update_requests import FileUpdateRequestStore, UpdateConflict
from planner.executor.update_configuration import (
    MAC_RUNTIME_ARCHIVE_NAME,
    MAC_RUNTIME_MANIFEST_NAME,
    MAX_MAC_RUNTIME_ARCHIVE_BYTES,
    MAX_MAC_RUNTIME_EXPANDED_BYTES,
    MAX_MAC_RUNTIME_MANIFEST_BYTES,
    MAX_MAC_UPDATE_DETAIL_CHARACTERS,
)
from planner.executor.update_contracts import (
    MAC_UPDATE_SCHEMA_VERSION,
    MacUpdateCheck,
    MacUpdateReport,
    MacUpdateStatus,
    MacUpdateTarget,
)


class MacUpdateService:
    """One authenticated executor's target, archive, and independent progress receipt."""

    def __init__(
        self,
        root: Path,
        store: UpdateRequestStore,
        provider: UpdateProvider | None,
        current_app_sha: str | None,
    ) -> None:
        self.root, self.store, self.provider = root, store, provider
        self.current_app_sha = current_app_sha
        self._applied = FileUpdateRequestStore(root / "applied.json")
        self._lock = threading.RLock()
        self._retain_applied(self.store.read())

    def _archive_path(self, release: PublishedUpdate) -> Path:
        mac = release.mac
        if (
            mac is None
            or type(mac.artifact_id) is not int
            or mac.artifact_id < 1
            or not isinstance(mac.archive_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", mac.archive_sha256) is None
            or type(mac.archive_size) is not int
            or not 0 < mac.archive_size <= MAX_MAC_RUNTIME_ARCHIVE_BYTES
            or re.fullmatch(r"[0-9a-f]{40}", release.manifest.app_sha) is None
        ):
            raise UpdateConflict("The Mac publication identity is invalid.")
        return self.root / "archives" / (mac.archive_sha256 + ".tar.gz")

    @staticmethod
    def _validate_archive(path: Path, release: PublishedUpdate) -> None:
        mac = release.mac
        assert mac is not None
        if path.stat().st_size != mac.archive_size:
            raise UpdateConflict("The Mac runtime archive size differs from its publication.")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != mac.archive_sha256:
            raise UpdateConflict("The Mac runtime archive differs from its publication.")
        total = 0
        manifest: object = None
        python_exists = False
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                total += member.size
                if total > MAX_MAC_RUNTIME_EXPANDED_BYTES:
                    raise UpdateConflict("The expanded Mac runtime exceeds its size limit.")
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise UpdateConflict("The Mac runtime contains an invalid path.")
                if str(name) == MAC_RUNTIME_MANIFEST_NAME:
                    if (
                        manifest is not None
                        or not member.isfile()
                        or member.size > MAX_MAC_RUNTIME_MANIFEST_BYTES
                    ):
                        raise UpdateConflict("The Mac runtime manifest is invalid.")
                    manifest_stream = archive.extractfile(member)
                    assert manifest_stream is not None
                    manifest = json.load(manifest_stream)
                if str(name) == "python/bin/python3":
                    python_exists = member.isfile() or member.issym()
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or manifest.get("application_revision") != release.manifest.app_sha
            or manifest.get("platform") != "darwin"
            or manifest.get("architecture") != "arm64"
            or not python_exists
        ):
            raise UpdateConflict("The Mac runtime does not match the approved source and platform.")

    def _retain_applied(self, request: OwnerUpdateRequest | None) -> None:
        if (
            request is not None
            and request.state == "applied"
            and request.release.mac is not None
            and request.release.manifest.app_sha == self.current_app_sha
            and self._archive_path(request.release).is_file()
        ):
            retained = self._applied.read()
            if retained is None or retained.request_id != request.request_id:
                self._applied.create(request)

    def prepare(self, release: PublishedUpdate) -> None:
        """Pin bytes before approval can dispatch or the provider can expire them."""
        try:
            self._prepare(release)
        except (tarfile.TarError, zipfile.BadZipFile) as error:
            raise UpdateConflict("The Mac runtime archive is invalid.") from error

    def _prepare(self, release: PublishedUpdate) -> None:
        with self._lock:
            # Preserve the last success before the single request record is replaced.
            self._retain_applied(self.store.read())
            if release.mac is None:
                return
            destination = self._archive_path(release)
            if destination.is_file():
                self._validate_archive(destination, release)
                return
            if self.provider is None:
                raise UpdateConflict("The Mac runtime provider is unavailable.")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(
                prefix=".download-", dir=destination.parent
            ) as temporary:
                downloaded = Path(temporary) / "artifact.zip"
                self.provider.download_artifact(release.mac.artifact_id, downloaded)
                candidate = Path(temporary) / MAC_RUNTIME_ARCHIVE_NAME
                with zipfile.ZipFile(downloaded) as artifact:
                    entries = artifact.infolist()
                    names = {entry.filename for entry in entries}
                    if (
                        len(names) != len(entries)
                        or names not in (
                            {MAC_RUNTIME_ARCHIVE_NAME},
                            {MAC_RUNTIME_ARCHIVE_NAME, "mac-runtime-publication.json"},
                        )
                    ):
                        raise UpdateConflict("The Mac runtime artifact is invalid.")
                    runtime = artifact.getinfo(MAC_RUNTIME_ARCHIVE_NAME)
                    if runtime.file_size != release.mac.archive_size:
                        raise UpdateConflict("The Mac runtime artifact size differs from approval.")
                    # The owner-approved VPS proof pins these bytes. The optional
                    # build proof travels beside them but grants no separate authority.
                    with artifact.open(runtime) as source, candidate.open("xb") as output:
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                self._validate_archive(candidate, release)
                os.replace(candidate, destination)
                directory = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)

    def _selected_request(self) -> OwnerUpdateRequest | None:
        request = self.store.read()
        self._retain_applied(request)
        if request is not None and request.state not in {"cancelled", "failed"}:
            return request
        retained = self._applied.read()
        if retained is not None and retained.release.manifest.app_sha == self.current_app_sha:
            return retained
        return None

    def check(self) -> MacUpdateCheck:
        with self._lock:
            request = self._selected_request()
            target = None
            if request is not None and request.release.mac is not None:
                archive = self._archive_path(request.release)
                if not archive.is_file():
                    raise UpdateConflict("The approved Mac runtime archive is unavailable.")
                mac = request.release.mac
                target = MacUpdateTarget(
                    request.request_id,
                    request.release.manifest.app_sha,
                    request.mode,
                    request.state == "applied"
                    and request.release.manifest.app_sha == self.current_app_sha,
                    mac.archive_sha256,
                    mac.archive_size,
                    f"/api/executor/update/{request.request_id}/archive",
                )
            return MacUpdateCheck(MAC_UPDATE_SCHEMA_VERSION, target)

    def _report_path(self, request_id: str) -> Path:
        return self.root / "reports" / (hashlib.sha256(request_id.encode()).hexdigest() + ".json")

    def status(self) -> MacUpdateStatus | None:
        with self._lock:
            request = self._selected_request()
            if request is None:
                return None
            revision = request.release.manifest.app_sha
            if request.release.mac is None:
                return MacUpdateStatus(
                    request.request_id,
                    revision,
                    "unavailable",
                    "This publication has no Mac runtime. The Mac requires a manual update.",
                )
            try:
                value = json.loads(self._report_path(request.request_id).read_text())
            except FileNotFoundError:
                return MacUpdateStatus(
                    request.request_id,
                    revision,
                    "waiting",
                    "The Mac waits for this approved update.",
                )
            return MacUpdateStatus(**value["status"])

    def ready_for_dispatch(self, request: OwnerUpdateRequest, health: Mapping[str, object]) -> bool:
        """A sleeping Mac catches up later. A connected Mac first confirms its stop."""
        if (
            request.mode != "now"
            or request.release.mac is None
            or health.get("connection_state") not in {"ready", "reconciling"}
        ):
            return True
        with self._lock:
            try:
                saved = json.loads(self._report_path(request.request_id).read_text())
            except FileNotFoundError:
                return False
            return bool(
                saved["status"]["request_id"] == request.request_id
                and saved["status"]["target_revision"] == request.release.manifest.app_sha
                and saved["status"]["phase"] == "ready_to_restart"
                and saved["identity"] is not None
                and saved["identity"] == health.get("identity")
            )

    def report(self, report: MacUpdateReport, health: Mapping[str, object]) -> MacUpdateStatus:
        with self._lock:
            target = self.check().target
            if (
                target is None
                or report.request_id != target.request_id
                or report.application_revision != target.application_revision
                or (
                    report.detail is not None
                    and len(report.detail) > MAX_MAC_UPDATE_DETAIL_CHARACTERS
                )
            ):
                raise UpdateConflict("The Mac update report does not match its approved target.")
            if report.phase == "current" and (
                not target.activate
                or report.identity is None
                or report.identity.application_revision != target.application_revision
                or health.get("connection_state") != "ready"
                or health.get("identity") != asdict(report.identity)
            ):
                raise UpdateConflict("The target Mac runtime did not report a ready connection.")
            if report.phase == "ready_to_restart" and (
                report.identity is None
                or health.get("connection_state") not in {"ready", "reconciling"}
                or health.get("identity") != asdict(report.identity)
            ):
                raise UpdateConflict("The Mac stop report does not match its current connection.")
            result = MacUpdateStatus(
                target.request_id, target.application_revision, report.phase, report.detail
            )
            _atomic_write(
                self._report_path(target.request_id),
                {
                    "status": asdict(result),
                    "identity": None if report.identity is None else asdict(report.identity),
                },
            )
            change_signal.emit()
            return result

    def archive(self, request_id: str) -> Path:
        with self._lock:
            selected = self._selected_request()
            retained = self._applied.read()
            for request in (selected, retained):
                if request is not None and request.request_id == request_id:
                    path = self._archive_path(request.release)
                    if path.is_file():
                        return path
            raise UpdateConflict("The requested Mac runtime is not an approved target.")
