"""Sanitized, host-local operational status and proof-bound maintenance.

This module deliberately has no HTTP or Click dependency.  The server and the
SSH-usable command both serialize its one immutable snapshot; cleanup stays a
direct host filesystem operation.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from planner.core.config import Config
from planner.environments.backup import (
    apply_verified_snapshot_retention,
    verified_snapshot_retention_plan,
    verified_snapshots,
)

StatusState = Literal["healthy", "warning", "critical", "unavailable", "review_needed"]


@dataclass(frozen=True)
class VpsStatusPolicy:
    """The one policy value used by status classification and cleanup proof."""

    disk_warning_free_ratio: float = 0.15
    disk_warning_free_bytes: int = 10 * 1024**3
    disk_critical_free_ratio: float = 0.08
    disk_critical_free_bytes: int = 5 * 1024**3
    backup_warning_age: timedelta = timedelta(hours=36)
    backup_critical_age: timedelta = timedelta(hours=72)
    log_warning_bytes: int = 25 * 1024**2
    temporary_expiry: timedelta = timedelta(hours=24)


DEFAULT_VPS_STATUS_POLICY = VpsStatusPolicy()


def resolve_status_application_root(environment: dict[str, str] | None = None) -> Path:
    """Use launcher-provided release context, or the installed/source application root.

    The path is evidence context for the bounded Git probe only; release identity
    remains ``Config.release_sha``.
    """
    values = os.environ if environment is None else environment
    release_root = values.get("PLAN_RELEASE_ROOT")
    if release_root is not None:
        return Path(release_root).expanduser().resolve(strict=False)
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class VpsStatusDependencies:
    """Small adapters that keep collection deterministic and testable."""

    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    platform_name: Callable[[], str] = platform.system
    statvfs: Callable[[str], os.statvfs_result] = os.statvfs
    process_lines: Callable[[], Iterable[str]] | None = None
    git_worktree_output: Callable[[Path], str] | None = None
    live_reference_paths: Callable[[], Iterable[Path]] = lambda: ()


@dataclass(frozen=True)
class VpsStatusSnapshot:
    collected_at: str
    overall_state: StatusState
    environment: dict[str, object]
    release: dict[str, object]
    backup: dict[str, object]
    disk: dict[str, object]
    workloads: dict[str, object]
    worktrees: dict[str, object]
    logs: dict[str, object]
    cleanup_candidates: dict[str, object]
    resources: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "collected_at": self.collected_at,
            "overall_state": self.overall_state,
            "environment": self.environment,
            "release": self.release,
            "backup": self.backup,
            "disk": self.disk,
            "workloads": self.workloads,
            "worktrees": self.worktrees,
            "logs": self.logs,
            "cleanup_candidates": self.cleanup_candidates,
            "resources": self.resources,
        }


@dataclass(frozen=True)
class CleanupCandidate:
    kind: Literal["log_rotation", "backup_retention", "temporary_backup"]
    path: Path
    root: Path
    expires_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "path": str(self.path), "expires_at": _iso(self.expires_at)}


@dataclass(frozen=True)
class CleanupInventory:
    """One fresh, in-memory inventory for exactly one command invocation."""

    candidates: tuple[CleanupCandidate, ...]
    policy: VpsStatusPolicy
    dependencies: VpsStatusDependencies

    def as_dict(self) -> dict[str, object]:
        return {"candidates": [candidate.as_dict() for candidate in self.candidates]}


@dataclass(frozen=True)
class CleanupResult:
    removed: list[str]
    rotated: list[str]
    review_needed: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "removed": self.removed,
            "rotated": self.rotated,
            "review_needed": self.review_needed,
        }


def collect_vps_status(
    config: Config,
    *,
    dependencies: VpsStatusDependencies | None = None,
    policy: VpsStatusPolicy = DEFAULT_VPS_STATUS_POLICY,
    application_root: Path | None = None,
) -> VpsStatusSnapshot:
    """Collect a bounded, JSON-safe status snapshot without opening secrets."""
    dependencies = dependencies or VpsStatusDependencies()
    now = _utc(dependencies.now())
    runtime_root = Path(config.db_path).expanduser().resolve(strict=False).parent
    backup_root = Path(config.backup_dir).expanduser()
    logs_root = Path(config.logs_dir).expanduser()
    inventory = collect_cleanup_inventory(config, dependencies=dependencies, policy=policy)
    environment = _environment_section(runtime_root)
    release = _release_section(config.release_sha)
    backup = _backup_section(backup_root, now, policy)
    disk = _disk_section(runtime_root, dependencies, policy)
    workloads = _workloads_section(dependencies)
    worktrees = _worktrees_section(
        application_root or resolve_status_application_root(), dependencies
    )
    logs = _logs_section(logs_root, policy)
    cleanup_candidates: dict[str, object] = {
        "state": "healthy" if not inventory.candidates else "warning",
        "summary": "no eligible cleanup"
        if not inventory.candidates
        else "maintenance candidates need review",
        "candidates": [candidate.as_dict() for candidate in inventory.candidates],
    }
    resources = _resources_section(dependencies)
    sections = (
        environment,
        release,
        backup,
        disk,
        workloads,
        worktrees,
        logs,
        cleanup_candidates,
        resources,
    )
    return VpsStatusSnapshot(
        collected_at=_iso(now) or "",
        overall_state=_overall_state(section["state"] for section in sections),
        environment=environment,
        release=release,
        backup=backup,
        disk=disk,
        workloads=workloads,
        worktrees=worktrees,
        logs=logs,
        cleanup_candidates=cleanup_candidates,
        resources=resources,
    )


def collect_cleanup_inventory(
    config: Config,
    *,
    dependencies: VpsStatusDependencies | None = None,
    policy: VpsStatusPolicy = DEFAULT_VPS_STATUS_POLICY,
) -> CleanupInventory:
    """Build one fresh inventory; it grants no authority to a later invocation."""
    dependencies = dependencies or VpsStatusDependencies()
    now = _utc(dependencies.now())
    backup_root = Path(config.backup_dir).expanduser()
    logs_root = Path(config.logs_dir).expanduser()
    candidates: list[CleanupCandidate] = []
    if backup_root.is_dir() and not backup_root.is_symlink():
        for path in verified_snapshot_retention_plan(backup_root):
            candidates.append(CleanupCandidate("backup_retention", path, backup_root))
        try:
            backup_children = _bounded_children(backup_root)
        except OSError:
            backup_children = []
        for path in backup_children:
            if not path.name.startswith(".backup-") or not _expired_directory(path, now, policy):
                continue
            candidates.append(
                CleanupCandidate(
                    "temporary_backup",
                    path,
                    backup_root,
                    _utc(datetime.fromtimestamp(path.stat().st_mtime, UTC))
                    + policy.temporary_expiry,
                )
            )
    if logs_root.is_dir() and not logs_root.is_symlink():
        try:
            log_children = _bounded_children(logs_root)
        except OSError:
            log_children = []
        for path in log_children:
            if (
                _safe_regular_under(logs_root, path)
                and path.stat().st_size >= policy.log_warning_bytes
            ):
                candidates.append(CleanupCandidate("log_rotation", path, logs_root))
    return CleanupInventory(tuple(candidates), policy, dependencies)


def apply_cleanup_inventory(inventory: CleanupInventory) -> CleanupResult:
    """Apply exactly this inventory, immediately re-proving every target first."""
    removed: list[str] = []
    rotated: list[str] = []
    review_needed: list[str] = []
    backup_candidates: list[CleanupCandidate] = []
    for candidate in inventory.candidates:
        if candidate.kind == "backup_retention":
            backup_candidates.append(candidate)
            continue
        if candidate.kind == "temporary_backup":
            if not _temporary_candidate_is_still_safe(candidate, inventory):
                review_needed.append(str(candidate.path))
                continue
            shutil.rmtree(candidate.path)
            removed.append(str(candidate.path))
            continue
        if not _safe_regular_under(candidate.root, candidate.path):
            review_needed.append(str(candidate.path))
            continue
        rotated_path = candidate.path.with_name(candidate.path.name + ".1")
        if (
            rotated_path.exists()
            or rotated_path.is_symlink()
            or not _contained(candidate.root, rotated_path)
        ):
            review_needed.append(str(candidate.path))
            continue
        os.replace(candidate.path, rotated_path)
        rotated.append(str(candidate.path))
    if backup_candidates:
        root = backup_candidates[0].root
        planned = tuple(candidate.path for candidate in backup_candidates)
        if any(candidate.root != root for candidate in backup_candidates):
            review_needed.extend(str(candidate.path) for candidate in backup_candidates)
        else:
            try:
                apply_verified_snapshot_retention(root, planned)
            except (OSError, ValueError):
                review_needed.extend(str(candidate.path) for candidate in backup_candidates)
            else:
                removed.extend(str(path) for path in planned)
    return CleanupResult(removed=removed, rotated=rotated, review_needed=review_needed)


def _environment_section(runtime_root: Path) -> dict[str, object]:
    if not runtime_root.is_dir():
        return {"state": "unavailable", "summary": "runtime path is unavailable"}
    return {"state": "healthy", "summary": "runtime path is available"}


def _release_section(release_sha: str | None) -> dict[str, object]:
    if release_sha is None:
        return {
            "state": "review_needed",
            "summary": "release identity is not configured",
            "sha": None,
        }
    return {"state": "healthy", "summary": "configured release identity", "sha": release_sha}


def _backup_section(root: Path, now: datetime, policy: VpsStatusPolicy) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        return {
            "state": "unavailable",
            "summary": "backup directory is unavailable",
            "verified_snapshot_count": 0,
            "latest_verified_at": None,
        }
    snapshots = verified_snapshots(root)
    if not snapshots:
        return {
            "state": "critical",
            "summary": "no verified backup snapshot",
            "verified_snapshot_count": 0,
            "latest_verified_at": None,
        }
    latest = snapshots[0]
    try:
        import json

        created = json.loads((latest / "metadata.json").read_text(encoding="utf-8"))["created_at"]
        created_at = datetime.fromisoformat(created)
        age = now - _utc(created_at)
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "state": "review_needed",
            "summary": "verified backup age is unavailable",
            "verified_snapshot_count": len(snapshots),
            "latest_verified_at": None,
        }
    state: StatusState = "healthy"
    if age > policy.backup_critical_age:
        state = "critical"
    elif age > policy.backup_warning_age:
        state = "warning"
    return {
        "state": state,
        "summary": "latest verified backup",
        "verified_snapshot_count": len(snapshots),
        "latest_verified_at": _iso(created_at),
        "age_seconds": int(age.total_seconds()),
    }


def _disk_section(
    root: Path, dependencies: VpsStatusDependencies, policy: VpsStatusPolicy
) -> dict[str, object]:
    if not root.is_dir():
        return {
            "state": "unavailable",
            "summary": "runtime disk path is unavailable",
            "free_bytes": None,
            "total_bytes": None,
            "free_percent": None,
        }
    try:
        stats = dependencies.statvfs(str(root))
        total = stats.f_frsize * stats.f_blocks
        free = stats.f_frsize * stats.f_bavail
    except OSError:
        return {
            "state": "unavailable",
            "summary": "runtime disk probe failed",
            "free_bytes": None,
            "total_bytes": None,
            "free_percent": None,
        }
    if total <= 0:
        return {
            "state": "review_needed",
            "summary": "runtime disk capacity is invalid",
            "free_bytes": free,
            "total_bytes": total,
            "free_percent": None,
        }
    ratio = free / total
    state: StatusState = "healthy"
    if ratio < policy.disk_critical_free_ratio or free < policy.disk_critical_free_bytes:
        state = "critical"
    elif ratio < policy.disk_warning_free_ratio or free < policy.disk_warning_free_bytes:
        state = "warning"
    return {
        "state": state,
        "summary": "runtime disk capacity",
        "free_bytes": free,
        "total_bytes": total,
        "free_percent": round(ratio * 100, 2),
    }


def _workloads_section(dependencies: VpsStatusDependencies) -> dict[str, object]:
    try:
        lines = list((dependencies.process_lines or _default_process_lines)())[:128]
    except (OSError, subprocess.SubprocessError):
        return {"state": "unavailable", "summary": "process probe failed", "items": []}
    items: list[dict[str, object]] = []
    for line in lines:
        fields = line.split(maxsplit=3)
        if len(fields) != 4:
            continue
        pid, _parent, elapsed, command = fields
        if not pid.isdecimal() or len(elapsed) > 32:
            continue
        role = _recognised_panels_workload_role(command)
        if role is not None:
            items.append({"role": role, "pid": int(pid), "state": "running", "age": elapsed})
    return {"state": "healthy", "summary": "recognised Panels workloads", "items": items}


def _recognised_panels_workload_role(command: str) -> str | None:
    """Recognise supported launcher forms without retaining command arguments."""
    command_tokens = command.split()
    if not command_tokens:
        return None
    executable = Path(command_tokens[0]).name
    if executable in {"planner", "panels"} and command_tokens[1:2] == ["serve"]:
        return "planner"
    if (
        executable.startswith("python")
        and command_tokens[1:4] == ["-m", "planner", "serve"]
    ):
        return "planner"
    return None


def _worktrees_section(root: Path | None, dependencies: VpsStatusDependencies) -> dict[str, object]:
    if root is None or not root.is_dir():
        return {
            "state": "unavailable",
            "summary": "repository worktree proof is unavailable",
            "items": [],
        }
    try:
        output = (dependencies.git_worktree_output or _default_git_worktree_output)(root)
    except (OSError, subprocess.SubprocessError):
        return {"state": "unavailable", "summary": "git worktree probe failed", "items": []}
    items = []
    for block in output[:32768].split("\n\n")[:128]:
        first = block.splitlines()[0] if block.splitlines() else ""
        if not first.startswith("worktree "):
            continue
        candidate = Path(first.removeprefix("worktree "))
        if not candidate.is_dir():
            continue
        items.append({"state": "proven"})
    return {
        "state": "healthy",
        "summary": "git-proven worktrees",
        "count": len(items),
        "items": items,
    }


def _logs_section(root: Path, policy: VpsStatusPolicy) -> dict[str, object]:
    if not root.is_dir() or root.is_symlink():
        return {
            "state": "unavailable",
            "summary": "log directory is unavailable",
            "file_count": 0,
            "total_bytes": None,
        }
    total = 0
    count = 0
    try:
        for path in _bounded_children(root):
            if not _safe_regular_under(root, path):
                continue
            total += path.stat().st_size
            count += 1
    except OSError:
        return {
            "state": "review_needed",
            "summary": "log directory could not be fully inspected",
            "file_count": count,
            "total_bytes": total,
        }
    return {
        "state": "warning" if total >= policy.log_warning_bytes else "healthy",
        "summary": "configured log usage",
        "file_count": count,
        "total_bytes": total,
    }


def _resources_section(dependencies: VpsStatusDependencies) -> dict[str, object]:
    platform_name = dependencies.platform_name()
    summary = (
        "resource collection is unavailable on macOS"
        if platform_name == "Darwin"
        else "Linux resource collection is deferred"
    )
    return {
        "state": "unavailable",
        "summary": summary,
        "cpu_percent": None,
        "load_averages": None,
        "ram": None,
        "swap": None,
    }


def _temporary_candidate_is_still_safe(
    candidate: CleanupCandidate, inventory: CleanupInventory
) -> bool:
    now = _utc(inventory.dependencies.now())
    if not _safe_directory_under(
        candidate.root, candidate.path
    ) or not candidate.path.name.startswith(".backup-"):
        return False
    if not _expired_directory(candidate.path, now, inventory.policy):
        return False
    resolved_references = {
        Path(path).expanduser().resolve(strict=False)
        for path in inventory.dependencies.live_reference_paths()
    }
    return candidate.path.resolve() not in resolved_references


def _bounded_children(root: Path) -> list[Path]:
    return list(root.iterdir())[:1024]


def _safe_regular_under(root: Path, path: Path) -> bool:
    return (
        root.is_dir()
        and not root.is_symlink()
        and not path.is_symlink()
        and path.is_file()
        and _contained(root, path)
    )


def _safe_directory_under(root: Path, path: Path) -> bool:
    return (
        root.is_dir()
        and not root.is_symlink()
        and not path.is_symlink()
        and path.is_dir()
        and _contained(root, path)
    )


def _contained(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _expired_directory(path: Path, now: datetime, policy: VpsStatusPolicy) -> bool:
    try:
        return (
            _utc(datetime.fromtimestamp(path.stat().st_mtime, UTC)) + policy.temporary_expiry <= now
        )
    except OSError:
        return False


def _default_process_lines() -> Iterable[str]:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,etime=,command="],
        check=True,
        capture_output=True,
        text=True,
        timeout=1,
    )
    return completed.stdout[:8192].splitlines()


def _default_git_worktree_output(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        timeout=1,
    )
    return completed.stdout[:32768]


def _overall_state(states: Iterable[object]) -> StatusState:
    values = set(states)
    for candidate in ("critical", "warning", "review_needed", "healthy", "unavailable"):
        if candidate in values:
            return candidate
    return "unavailable"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None
