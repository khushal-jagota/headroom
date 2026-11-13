"""Retain the Mac runtime of each update independently of publication expiry and VPS rollback.

Layout under `<data>/mac-updates`: `archives/<archive_sha256>.tar.gz` holds pinned
runtimes, `reports/<sha256(request_id)>.json` the Mac's last report per target, and
`applied.json` the last done attempt whose release the VPS runs. The 5622d2a81 server
used the same archive and report layout, which the bridge relies on.
"""

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
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path, PurePosixPath
from uuid import UUID

from planner.core import change_signal
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    UpdateConflict,
    atomic_write_json,
    attempt_to_dict,
    parse_attempt,
)
from planner.environments.update_contracts import (
    PublishedLinuxExecutorUpdate,
    PublishedMacUpdate,
    PublishedUpdate,
    UpdateAttempt,
    UpdateAttemptState,
    UpdateProvider,
)
from planner.executor.contracts import ExecutorHealthRead
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
    MacUpdatePhase,
    MacUpdatePrefetch,
    MacUpdateReport,
    MacUpdateStatus,
    MacUpdateTarget,
    mac_prefetch_archive_path,
)
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID

# From these states on the VPS has stopped its own work for the attempt, so the Mac may too.
_MAC_TARGET_STATES: frozenset[UpdateAttemptState] = frozenset({"preparing", "installing", "done"})


class MacUpdateService:
    """One authenticated executor's target, archive, prefetch, and progress receipt."""

    def __init__(
        self,
        root: Path,
        attempts: FileUpdateAttemptStore,
        provider: UpdateProvider | None,
        current_app_sha: str | None,
        ready_release: Callable[[], PublishedUpdate | None],
        *,
        machine_id: str = ORIGINAL_MAC_MACHINE_ID,
        archive_root: Path | None = None,
        platform: str = "darwin",
        platform_for: Callable[[str], str] | None = None,
    ) -> None:
        self.root, self.attempts, self.provider = root, attempts, provider
        self.machine_id = machine_id
        self.platform = platform
        self.platform_for = platform_for
        self.archive_root = root if archive_root is None else archive_root
        self._machines: dict[str, MacUpdateService] = {}
        self.current_app_sha = current_app_sha
        self.ready_release = ready_release
        self._applied_path = root / "applied.json"
        self._lock = threading.RLock()
        self._retain_applied(self._attempt())

    def _attempt(self) -> UpdateAttempt | None:
        attempt = self.attempts.read()
        if (
            attempt is None or self.machine_id == ORIGINAL_MAC_MACHINE_ID
            or attempt.mac_attempt_id is None
        ):
            return attempt
        return replace(attempt, mac_attempt_id=f"{attempt.mac_attempt_id}.{self.machine_id}")

    def for_machine(self, machine_id: str) -> MacUpdateService:
        """Reports and targets belong to one machine. Archive bytes remain shared."""
        if machine_id == self.machine_id:
            return self
        UUID(machine_id)
        with self._lock:
            kept = self._machines.get(machine_id)
            if kept is None:
                kept = MacUpdateService(
                    self.root / "machines" / machine_id, self.attempts, self.provider,
                    self.current_app_sha, self.ready_release, machine_id=machine_id,
                    archive_root=self.archive_root,
                    platform=(self.platform_for(machine_id) if self.platform_for else "darwin"),
                    platform_for=self.platform_for,
                )
                self._machines[machine_id] = kept
            return kept

    def check_for(self, machine_id: str) -> MacUpdateCheck:
        return self.for_machine(machine_id).check()

    def _artifact(
        self, release: PublishedUpdate,
    ) -> PublishedMacUpdate | PublishedLinuxExecutorUpdate | None:
        return release.linux_executor if self.platform == "linux" else release.mac

    @property
    def _archive_name(self) -> str:
        return ("panels-linux-executor.tar.gz" if self.platform == "linux"
                else MAC_RUNTIME_ARCHIVE_NAME)

    @property
    def _proof_name(self) -> str:
        return ("linux-executor-publication.json" if self.platform == "linux"
                else "mac-runtime-publication.json")

    def _archive_path(self, release: PublishedUpdate) -> Path:
        mac = self._artifact(release)
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
        return self.archive_root / "archives" / (mac.archive_sha256 + ".tar.gz")

    def _validate_archive(self, path: Path, release: PublishedUpdate) -> None:
        mac = self._artifact(release)
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
                if str(name) == (".venv/bin/python" if self.platform == "linux"
                                  else "python/bin/python3"):
                    python_exists = member.isfile() or member.issym()
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or manifest.get("application_revision") != release.manifest.app_sha
            or manifest.get("platform") != self.platform
            or manifest.get("architecture") != ("x86_64" if self.platform == "linux" else "arm64")
            or (self.platform == "linux" and manifest.get("role") != "executor")
            or not python_exists
        ):
            raise UpdateConflict("The Mac runtime does not match its source and platform.")

    def _read_applied(self) -> UpdateAttempt | None:
        try:
            return parse_attempt(json.loads(self._applied_path.read_text()))
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError):
            # The 5622d2a81 server kept a request here; it names a release this server
            # does not run, so it can never be the retained target.
            return None

    def _retain_applied(self, attempt: UpdateAttempt | None) -> None:
        """Keep the last done attempt the VPS runs, so a later failed attempt cannot hide it."""
        if (
            attempt is not None
            and attempt.state == "done"
            and attempt.mac_attempt_id is not None
            and self._artifact(attempt.release) is not None
            and attempt.to_sha == self.current_app_sha
            and self._archive_path(attempt.release).is_file()
        ):
            retained = self._read_applied()
            if retained is None or (retained.attempt_id, retained.mac_attempt_id) != (
                attempt.attempt_id,
                attempt.mac_attempt_id,
            ):
                atomic_write_json(self._applied_path, attempt_to_dict(attempt))

    def prepare(self, release: PublishedUpdate) -> None:
        """Pin the bytes before the release shows as available or the provider expires them."""
        try:
            self._prepare(release)
            if self.platform == "darwin" and release.linux_executor is not None:
                # Pin both platform archives before the release becomes available.
                linux = MacUpdateService(
                    self.root, self.attempts, self.provider, self.current_app_sha,
                    self.ready_release, archive_root=self.archive_root, platform="linux",
                )
                linux._prepare(release)
        except (tarfile.TarError, zipfile.BadZipFile) as error:
            raise UpdateConflict("The Mac runtime archive is invalid.") from error

    def _prepare(self, release: PublishedUpdate) -> None:
        with self._lock:
            # Preserve the last success before a new attempt replaces the record.
            self._retain_applied(self._attempt())
            mac = self._artifact(release)
            if mac is None:
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
                self.provider.download_artifact(mac.artifact_id, downloaded)
                candidate = Path(temporary) / self._archive_name
                with zipfile.ZipFile(downloaded) as artifact:
                    entries = artifact.infolist()
                    names = {entry.filename for entry in entries}
                    if len(names) != len(entries) or names not in (
                        {self._archive_name},
                        {self._archive_name, self._proof_name},
                    ):
                        raise UpdateConflict("The Mac runtime artifact is invalid.")
                    runtime = artifact.getinfo(self._archive_name)
                    if runtime.file_size != mac.archive_size:
                        raise UpdateConflict("The Mac runtime artifact size differs.")
                    # The VPS publication receipt pins these bytes. The optional
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

    def _selected_attempt(self) -> UpdateAttempt | None:
        attempt = self._attempt()
        self._retain_applied(attempt)
        if (
            attempt is not None
            and attempt.state in _MAC_TARGET_STATES
            and self._artifact(attempt.release) is not None
            and attempt.mac_attempt_id is not None
            # A done release the VPS no longer runs would stop the Mac without activating.
            and (attempt.state != "done" or attempt.to_sha == self.current_app_sha)
        ):
            return attempt
        retained = self._read_applied()
        if retained is not None and retained.to_sha == self.current_app_sha:
            return retained
        return None

    def _target(self) -> MacUpdateTarget | None:
        attempt = self._selected_attempt()
        if attempt is None:
            return None
        mac, request_id = self._artifact(attempt.release), attempt.mac_attempt_id
        assert mac is not None and request_id is not None
        if not self._archive_path(attempt.release).is_file():
            raise UpdateConflict("The Mac runtime archive for this update is unavailable.")
        return MacUpdateTarget(
            request_id,
            attempt.to_sha,
            attempt.mode,
            attempt.state == "done" and attempt.to_sha == self.current_app_sha,
            mac.archive_sha256,
            mac.archive_size,
            # Mac runtimes in the field accept exactly this path for their target.
            f"/api/executor/update/{request_id}/archive",
        )

    def _prefetch_release(self) -> PublishedUpdate | None:
        """The ready release, while its Mac runtime is pinned here."""
        release = self.ready_release()
        if release is None or self._artifact(release) is None:
            return None
        try:
            return release if self._archive_path(release).is_file() else None
        except UpdateConflict:
            return None

    def check(self) -> MacUpdateCheck:
        with self._lock:
            release = self._prefetch_release()
            prefetch = None
            # Offered also while it is the target: a stopped Mac then activates from disk.
            mac = None if release is None else self._artifact(release)
            if release is not None and mac is not None:
                prefetch = MacUpdatePrefetch(
                    release.manifest.app_sha,
                    mac.archive_sha256,
                    mac.archive_size,
                    mac_prefetch_archive_path(mac.archive_sha256),
                )
            return MacUpdateCheck(MAC_UPDATE_SCHEMA_VERSION, self._target(), prefetch)

    def _report_path(self, request_id: str) -> Path:
        return self.root / "reports" / (hashlib.sha256(request_id.encode()).hexdigest() + ".json")

    def _saved_status(self, request_id: str) -> MacUpdateStatus | None:
        try:
            value = json.loads(self._report_path(request_id).read_text())
        except FileNotFoundError:
            return None
        return MacUpdateStatus(**value["status"])

    def status(self) -> MacUpdateStatus | None:
        with self._lock:
            current = self._attempt()
            if (
                current is not None
                and current.state in _MAC_TARGET_STATES
                and self._artifact(current.release) is None
            ):
                return MacUpdateStatus(
                    current.mac_attempt_id or current.attempt_id,
                    current.to_sha,
                    "unavailable",
                    "This publication has no runtime for this machine. "
                    "A manual update is required.",
                )
            attempt = self._selected_attempt()
            if attempt is None or attempt.mac_attempt_id is None:
                return None
            return self._saved_status(attempt.mac_attempt_id) or MacUpdateStatus(
                attempt.mac_attempt_id,
                attempt.to_sha,
                "waiting",
                "This machine waits for this update.",
            )

    def phase(self, mac_attempt_id: str) -> MacUpdatePhase | None:
        """The Mac's last reported phase for this target, if it reported one."""
        with self._lock:
            saved = self._saved_status(mac_attempt_id)
            return None if saved is None or saved.phase == "unavailable" else saved.phase

    def reported_at(self, mac_attempt_id: str) -> float | None:
        """When the Mac last reported on this target; it re-reports each poll while it waits."""
        try:
            return self._report_path(mac_attempt_id).stat().st_mtime
        except FileNotFoundError:
            return None

    def report(self, report: MacUpdateReport, health: ExecutorHealthRead | None) -> MacUpdateStatus:
        """Record a Mac's update progress. "current" and "ready_to_restart" must come from
        the process whose lease is current and fresh (health is the VPS's presence read)."""
        leased_identity = (
            health.identity if health is not None and health.presence == "present" else None
        )
        with self._lock:
            target = self._target()
            if (
                target is None
                or report.request_id != target.request_id
                or report.application_revision != target.application_revision
                or (
                    report.detail is not None
                    and len(report.detail) > MAX_MAC_UPDATE_DETAIL_CHARACTERS
                )
            ):
                raise UpdateConflict("The Mac update report does not match its target.")
            if report.phase == "current" and (
                not target.activate
                or report.identity is None
                or report.identity.application_revision != target.application_revision
                or leased_identity != report.identity
            ):
                raise UpdateConflict("The target Mac runtime is not the present, leased one.")
            if report.phase == "ready_to_restart" and (
                report.identity is None or leased_identity != report.identity
            ):
                raise UpdateConflict("The Mac stop report does not match its present process.")
            result = MacUpdateStatus(
                target.request_id, target.application_revision, report.phase, report.detail
            )
            atomic_write_json(
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
            for attempt in (self._selected_attempt(), self._read_applied()):
                if attempt is not None and attempt.mac_attempt_id == request_id:
                    path = self._archive_path(attempt.release)
                    if path.is_file():
                        return path
            raise UpdateConflict("The requested Mac runtime is not an update target.")

    def prefetch_archive(self, archive_sha256: str) -> Path:
        with self._lock:
            release = self._prefetch_release()
            mac = None if release is None else self._artifact(release)
            if release is None or mac is None or mac.archive_sha256 != archive_sha256:
                raise UpdateConflict("The requested Mac runtime is not the ready release.")
            return self._archive_path(release)
