"""Serialized, code-only release deployment transaction."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

import httpx

from planner.environments.release import (
    ReleaseManifest,
    ReleaseValidationError,
    validate_release_manifest,
)


class DeploymentError(RuntimeError):
    """Raised when deployment cannot safely proceed."""


class ServiceController(Protocol):
    def restart(self) -> None: ...


class HealthClient(Protocol):
    def wait_for_sha(self, sha: str, *, deadline: float) -> bool: ...


@dataclass(frozen=True)
class SubprocessServiceController:
    manager: str
    service_name: str

    def restart(self) -> None:
        if self.manager == "systemctl":
            command = ["systemctl", "restart", self.service_name]
        elif self.manager == "launchctl":
            command = ["launchctl", "kickstart", "-k", f"system/{self.service_name}"]
        else:
            raise DeploymentError("unsupported service manager")
        subprocess.run(command, check=True, shell=False)


@dataclass(frozen=True)
class HttpHealthClient:
    url: str
    poll_seconds: float = 0.25

    def wait_for_sha(self, sha: str, *, deadline: float) -> bool:
        while time.monotonic() < deadline:
            try:
                response = httpx.get(self.url, params={"expected_sha": sha}, timeout=1.0)
                if response.status_code == 200 and response.json().get("release_sha") == sha:
                    return True
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(self.poll_seconds)
        return False


@dataclass(frozen=True)
class DeploymentResult:
    status: str
    requested_sha: str
    prior_sha: str | None
    detail: str | None


class deployment_lock:
    """Operator-owned inter-process deployment lock."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self._stream: TextIO | None = None

    def __enter__(self) -> deployment_lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+", encoding="utf-8")
        fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_: object) -> None:
        assert self._stream is not None
        fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()


def deploy_release(
    *,
    candidate: Path,
    current_pointer: Path,
    backup: Callable[[str], object],
    service: ServiceController,
    health: HealthClient,
    records_path: Path,
    now: Callable[[], float] = time.monotonic,
    health_timeout_seconds: float = 30.0,
    release_root: Path | None = None,
    lock_path: Path | None = None,
    source_db: Path | None = None,
    baseline_sha: str | None = None,
) -> DeploymentResult:
    if health_timeout_seconds <= 0:
        raise DeploymentError("health timeout must be positive")
    candidate_sha = candidate.name
    effective_release_root = (
        release_root.expanduser().resolve()
        if release_root is not None
        else candidate.parent.resolve()
    )
    effective_lock_path = lock_path or (effective_release_root.parent / ".panels-deploy.lock")
    with deployment_lock(effective_lock_path):
        try:
            candidate_manifest = _validate_candidate(candidate, effective_release_root)
            prior_manifest = _validate_current(current_pointer, effective_release_root)
        except DeploymentError as exc:
            failed = DeploymentResult("failed", candidate_sha, None, str(exc))
            _record(records_path, failed, now())
            raise
        if (
            prior_manifest is not None
            and prior_manifest.release_sha == candidate_manifest.release_sha
        ):
            result = DeploymentResult(
                "unchanged", candidate_manifest.release_sha, prior_manifest.release_sha, None
            )
            _record(records_path, result, now())
            return result
        prior_sha = prior_manifest.release_sha if prior_manifest is not None else None
        if prior_sha is None:
            if source_db is not None and source_db.exists() and baseline_sha is None:
                raise DeploymentError(
                    "existing database requires an operator-established baseline SHA"
                )
            try:
                if source_db is not None and source_db.exists():
                    assert baseline_sha is not None
                    backup(baseline_sha)
                _switch_pointer(current_pointer, candidate)
                service.restart()
                if not health.wait_for_sha(
                    candidate_manifest.release_sha, deadline=now() + health_timeout_seconds
                ):
                    raise DeploymentError("initial release did not become healthy before cutoff")
            except BaseException as exc:
                current_pointer.unlink(missing_ok=True)
                result = DeploymentResult(
                    "initial_failed",
                    candidate_manifest.release_sha,
                    None,
                    f"{exc}; operator state path: {current_pointer}",
                )
                _record(records_path, result, now())
                return result
            result = DeploymentResult("succeeded", candidate_manifest.release_sha, None, None)
            _record(records_path, result, now())
            return result
        try:
            backup(prior_sha)
        except BaseException as exc:
            result = DeploymentResult(
                "failed", candidate_manifest.release_sha, prior_sha, "backup failed"
            )
            _record(records_path, result, now())
            raise DeploymentError("database backup failed; current release was unchanged") from exc
        prior_root = current_pointer.resolve()
        try:
            _switch_pointer(current_pointer, candidate)
        except BaseException as exc:
            result = DeploymentResult("failed", candidate_manifest.release_sha, prior_sha, str(exc))
            _record(records_path, result, now())
            raise
        try:
            service.restart()
            if not health.wait_for_sha(
                candidate_manifest.release_sha, deadline=now() + health_timeout_seconds
            ):
                raise DeploymentError("candidate release did not become healthy before cutoff")
        except BaseException as exc:
            rollback_detail = _rollback(
                current_pointer=current_pointer,
                prior_root=prior_root,
                service=service,
                health=health,
                now=now,
                timeout_seconds=health_timeout_seconds,
                original=exc,
            )
            if rollback_detail is not None:
                result = DeploymentResult(
                    "rollback_failed", candidate_manifest.release_sha, prior_sha, rollback_detail
                )
                _record(records_path, result, now())
                raise DeploymentError(rollback_detail) from exc
            result = DeploymentResult(
                "rolled_back", candidate_manifest.release_sha, prior_sha, str(exc)
            )
            _record(records_path, result, now())
            return result
        result = DeploymentResult("succeeded", candidate_manifest.release_sha, prior_sha, None)
        _record(records_path, result, now())
        return result


def _validate_candidate(candidate: Path, release_root: Path) -> ReleaseManifest:
    try:
        if candidate.is_symlink():
            raise ReleaseValidationError("candidate release must not be a symlink")
        return validate_release_manifest(
            candidate / "manifest.json",
            expected_sha=candidate.name,
            release_root=release_root,
            require_runtime=True,
        )
    except ReleaseValidationError as exc:
        raise DeploymentError(f"candidate release is invalid: {exc}") from exc


def _validate_current(current_pointer: Path, release_root: Path) -> ReleaseManifest | None:
    if not current_pointer.exists() and not current_pointer.is_symlink():
        return None
    try:
        return validate_release_manifest(
            current_pointer / "manifest.json", release_root=release_root, require_runtime=True
        )
    except ReleaseValidationError as exc:
        raise DeploymentError(f"current release is invalid: {exc}") from exc


def _switch_pointer(current_pointer: Path, target: Path) -> None:
    current_pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = current_pointer.with_name(f".{current_pointer.name}.next-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target.resolve(), target_is_directory=True)
    os.replace(temporary, current_pointer)


def _rollback(
    *,
    current_pointer: Path,
    prior_root: Path,
    service: ServiceController,
    health: HealthClient,
    now: Callable[[], float],
    timeout_seconds: float,
    original: BaseException,
) -> str | None:
    try:
        _switch_pointer(current_pointer, prior_root)
        service.restart()
        prior_manifest = validate_release_manifest(current_pointer / "manifest.json")
        if not health.wait_for_sha(prior_manifest.release_sha, deadline=now() + timeout_seconds):
            return f"candidate failed ({original}); prior release did not become healthy"
    except BaseException as exc:
        return f"candidate failed ({original}); rollback failed: {exc}"
    return None


def _record(path: Path, result: DeploymentResult, timestamp: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "attempted_at": timestamp,
        "result": result.status,
        "requested_sha": result.requested_sha,
        "prior_sha": result.prior_sha,
        "detail": result.detail,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
