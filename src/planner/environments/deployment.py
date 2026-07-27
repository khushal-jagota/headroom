"""Serialized replacement of the one deployed application directory."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

import httpx

from planner.environments.app import AppManifest, AppValidationError, validate_app_manifest


class DeploymentError(RuntimeError):
    """Raised when app replacement cannot safely proceed."""


class ServiceController(Protocol):
    def stop(self) -> None: ...

    def restart(self) -> None: ...


class HealthClient(Protocol):
    def wait_for_sha(self, sha: str, *, deadline: float) -> bool: ...


Backup = Callable[[str], Path]
Restore = Callable[[Path], None]


@dataclass(frozen=True)
class SubprocessServiceController:
    manager: str
    service_name: str

    def stop(self) -> None:
        if self.manager == "systemctl":
            command = ["systemctl", "--user", "stop", self.service_name]
        elif self.manager == "launchctl":
            if not self.service_name.startswith("gui/"):
                raise DeploymentError(
                    "launchctl hard cutover requires an explicit gui/<uid>/<label> target"
                )
            command = ["/bin/launchctl", "bootout", self.service_name]
        else:
            raise DeploymentError("unsupported service manager")
        subprocess.run(command, check=True, shell=False)

    def restart(self) -> None:
        if self.manager == "systemctl":
            command = ["systemctl", "--user", "restart", self.service_name]
        elif self.manager == "launchctl":
            target = (
                self.service_name
                if "/" in self.service_name
                else f"system/{self.service_name}"
            )
            if target.startswith("gui/"):
                self._restart_user_launchagent(target)
                return
            command = ["/bin/launchctl", "kickstart", "-k", target]
        else:
            raise DeploymentError("unsupported service manager")
        subprocess.run(command, check=True, shell=False)

    @staticmethod
    def _restart_user_launchagent(target: str) -> None:
        domain, label = target.rsplit("/", 1)
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        subprocess.run(["/bin/launchctl", "kill", "SIGTERM", target], check=False, shell=False)
        deadline = time.monotonic() + 10.0
        while True:
            inspection = subprocess.run(
                ["/bin/launchctl", "print", target],
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            if inspection.returncode != 0 or re.search(
                r"(?m)^\s*pid = \d+\s*$", inspection.stdout
            ) is None:
                break
            if time.monotonic() >= deadline:
                raise DeploymentError("user LaunchAgent did not stop cleanly")
            time.sleep(0.05)
        subprocess.run(["/bin/launchctl", "bootout", target], check=False, shell=False)
        subprocess.run(
            ["/bin/launchctl", "bootstrap", domain, str(plist)], check=True, shell=False
        )


def run_current_app_backup(
    current_root: Path,
    source_db: Path,
    backup_dir: Path,
) -> Path:
    """Run a pre-deployment backup through the app that owns the live database."""
    current_app = current_root.expanduser().resolve() / "app"
    database = source_db.expanduser().resolve()
    backups = backup_dir.expanduser().resolve()
    result = subprocess.run(
        [
            str(current_app / "bin" / "panels-launcher"),
            "environment",
            "backup-current",
            "--source-db",
            str(database),
            "--backup-dir",
            str(backups),
            "--current-app",
            str(current_app),
        ],
        check=True,
        env=os.environ
        | {"PLAN_HERMES_HOME": str(Path.home().expanduser().resolve() / ".hermes")},
        shell=False,
        capture_output=True,
        text=True,
    )
    snapshot = result.stdout.strip()
    if not snapshot:
        raise DeploymentError("backup command did not report its snapshot path")
    return Path(snapshot).expanduser().resolve()


@dataclass(frozen=True)
class HttpHealthClient:
    url: str
    poll_seconds: float = 0.25

    def wait_for_sha(self, sha: str, *, deadline: float) -> bool:
        while time.monotonic() < deadline:
            try:
                response = httpx.get(self.url, params={"expected_sha": sha}, timeout=1.0)
                if response.status_code == 200 and response.json().get("app_sha") == sha:
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


def deploy_app(
    *,
    candidate_app: Path,
    current_root: Path,
    source_db: Path,
    backup: Backup,
    restore: Restore,
    service: ServiceController,
    health: HealthClient,
    now: Callable[[], float] = time.monotonic,
    health_timeout_seconds: float = 30.0,
    lock_path: Path | None = None,
) -> DeploymentResult:
    """Replace only ``current/app`` and recover the prior app on post-move failure."""
    if health_timeout_seconds <= 0:
        raise DeploymentError("health timeout must be positive")
    root_input = current_root.expanduser()
    if root_input.is_symlink():
        raise DeploymentError("current root must not be a symlink")
    root = root_input.resolve()
    current_app = root / "app"
    effective_lock_path = lock_path or (root.parent / ".panels-app-deploy.lock")
    with deployment_lock(effective_lock_path):
        _validate_current_root(root)
        candidate_manifest = _validate_app(
            candidate_app, "candidate app", require_runtime=True
        )
        prior_manifest = _validate_optional_current_app(current_app)
        if prior_manifest is None:
            if _persistent_state_exists(root):
                raise DeploymentError("persistent state exists without a current app")
            return _install_first_app(
                candidate_app=candidate_app,
                candidate_manifest=candidate_manifest,
                current_app=current_app,
                service=service,
                health=health,
                now=now,
                health_timeout_seconds=health_timeout_seconds,
            )
        if prior_manifest.app_sha == candidate_manifest.app_sha:
            if not health.wait_for_sha(
                prior_manifest.app_sha, deadline=now() + health_timeout_seconds
            ):
                raise DeploymentError("unchanged current app did not pass health proof")
            return DeploymentResult(
                "unchanged", candidate_manifest.app_sha, prior_manifest.app_sha, None
            )
        if not source_db.is_file():
            raise DeploymentError("current database is missing")
        staged = root / f".app-candidate-{os.getpid()}"
        fallback = root / f".app-fallback-{os.getpid()}"
        _require_unused(staged)
        _require_unused(fallback)
        try:
            shutil.copytree(candidate_app, staged, symlinks=True)
            _validate_app(
                staged,
                "staged candidate app",
                expected_sha=candidate_manifest.app_sha,
                require_runtime=True,
            )
            try:
                service.stop()
            except BaseException as exc:
                try:
                    service.restart()
                    if not health.wait_for_sha(
                        prior_manifest.app_sha,
                        deadline=now() + health_timeout_seconds,
                    ):
                        raise DeploymentError("prior app did not become healthy")
                except BaseException as recovery_exc:
                    raise DeploymentError(
                        f"service stop failed ({exc}); recovery failed: "
                        f"{recovery_exc}; operator continuation path: {current_app}"
                    ) from exc
                raise DeploymentError(
                    "service stop failed; current app was unchanged, restarted, and verified"
                ) from exc
            try:
                snapshot = backup(prior_manifest.app_sha)
            except BaseException as exc:
                try:
                    service.restart()
                    if not health.wait_for_sha(
                        prior_manifest.app_sha,
                        deadline=now() + health_timeout_seconds,
                    ):
                        raise DeploymentError("prior app did not become healthy")
                except BaseException as recovery_exc:
                    raise DeploymentError(
                        f"database backup failed ({exc}); recovery failed: "
                        f"{recovery_exc}; operator continuation path: {current_app}"
                    ) from exc
                raise DeploymentError(
                    "database backup failed; current app was unchanged and restarted"
                ) from exc
            try:
                os.replace(current_app, fallback)
            except BaseException as exc:
                try:
                    service.restart()
                    if not health.wait_for_sha(
                        prior_manifest.app_sha,
                        deadline=now() + health_timeout_seconds,
                    ):
                        raise DeploymentError("prior app did not become healthy")
                except BaseException as recovery_exc:
                    raise DeploymentError(
                        f"candidate cutover failed ({exc}); recovery failed: "
                        f"{recovery_exc}; operator continuation path: {current_app}"
                    ) from exc
                raise
            try:
                os.replace(staged, current_app)
                service.restart()
                if not health.wait_for_sha(
                    candidate_manifest.app_sha,
                    deadline=now() + health_timeout_seconds,
                ):
                    raise DeploymentError("candidate app did not become healthy before cutoff")
            except BaseException as exc:
                detail = _restore_fallback(
                    current_app=current_app,
                    fallback=fallback,
                    snapshot=snapshot,
                    restore=restore,
                    prior_manifest=prior_manifest,
                    service=service,
                    health=health,
                    now=now,
                    timeout_seconds=health_timeout_seconds,
                    original=exc,
                )
                if detail is not None:
                    raise DeploymentError(detail) from exc
                return DeploymentResult(
                    "rolled_back",
                    candidate_manifest.app_sha,
                    prior_manifest.app_sha,
                    str(exc),
                )
            shutil.rmtree(fallback)
            return DeploymentResult(
                "succeeded", candidate_manifest.app_sha, prior_manifest.app_sha, None
            )
        finally:
            if staged.exists() and not staged.is_symlink():
                shutil.rmtree(staged)


def _install_first_app(
    *,
    candidate_app: Path,
    candidate_manifest: AppManifest,
    current_app: Path,
    service: ServiceController,
    health: HealthClient,
    now: Callable[[], float],
    health_timeout_seconds: float,
) -> DeploymentResult:
    current_app.parent.mkdir(parents=True, exist_ok=True)
    staged = current_app.parent / f".app-candidate-{os.getpid()}"
    _require_unused(staged)
    try:
        shutil.copytree(candidate_app, staged, symlinks=True)
        _validate_app(
            staged,
            "staged candidate app",
            expected_sha=candidate_manifest.app_sha,
            require_runtime=True,
        )
        os.replace(staged, current_app)
        try:
            service.restart()
            if not health.wait_for_sha(
                candidate_manifest.app_sha, deadline=now() + health_timeout_seconds
            ):
                raise DeploymentError("initial app did not become healthy before cutoff")
        except BaseException as exc:
            if current_app.exists() and not current_app.is_symlink():
                shutil.rmtree(current_app)
            return DeploymentResult(
                "initial_failed",
                candidate_manifest.app_sha,
                None,
                f"{exc}; validated candidate remains at: {candidate_app}",
            )
        return DeploymentResult("succeeded", candidate_manifest.app_sha, None, None)
    finally:
        if staged.exists() and not staged.is_symlink():
            shutil.rmtree(staged)


def _validate_current_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)


def _validate_app(
    app: Path,
    label: str,
    *,
    expected_sha: str | None = None,
    require_runtime: bool = True,
) -> AppManifest:
    try:
        if app.is_symlink():
            raise AppValidationError(f"{label} must not be a symlink")
        return validate_app_manifest(
            app / "manifest.json",
            expected_sha=expected_sha,
            require_runtime=require_runtime,
        )
    except AppValidationError as exc:
        raise DeploymentError(f"{label} is invalid: {exc}") from exc


def _validate_optional_current_app(current_app: Path) -> AppManifest | None:
    if not current_app.exists() and not current_app.is_symlink():
        return None
    return _validate_app(current_app, "current app", require_runtime=False)


def _persistent_state_exists(current_root: Path) -> bool:
    return any(
        (current_root / name).exists() or (current_root / name).is_symlink()
        for name in ("data", "logs")
    )


def _require_unused(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise DeploymentError(f"temporary deployment path already exists: {path}")


def _restore_fallback(
    *,
    current_app: Path,
    fallback: Path,
    snapshot: Path,
    restore: Restore,
    prior_manifest: AppManifest,
    service: ServiceController,
    health: HealthClient,
    now: Callable[[], float],
    timeout_seconds: float,
    original: BaseException,
) -> str | None:
    failed = current_app.parent / f".app-failed-{os.getpid()}"
    try:
        service.stop()
        restore(snapshot)
        _require_unused(failed)
        if current_app.exists() or current_app.is_symlink():
            os.replace(current_app, failed)
        os.replace(fallback, current_app)
        service.restart()
        if not health.wait_for_sha(
            prior_manifest.app_sha, deadline=now() + timeout_seconds
        ):
            raise DeploymentError("prior app did not become healthy")
        if failed.exists() and not failed.is_symlink():
            shutil.rmtree(failed)
    except BaseException as exc:
        continuation_paths = [
            str(path)
            for path in (current_app, fallback, failed)
            if path.exists() or path.is_symlink()
        ]
        return (
            f"candidate failed ({original}); recovery failed: {exc}; "
            f"operator continuation paths: {', '.join(continuation_paths)}"
        )
    return None
