"""Durable, public-safe deployment lifecycle evidence.

The deployment runner owns this file.  Readers may outlive either the runner or
the application process, so the file is deliberately small, versioned, and
replaced atomically.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TextIO, cast

LIFECYCLE_VERSION = 1
MAX_LIFECYCLE_BYTES = 16 * 1024
MAX_DEPLOYMENT_ID_LENGTH = 128
MAX_DETAIL_LENGTH = 500
MAX_CODE_LENGTH = 64

NONTERMINAL_TTL = timedelta(minutes=15)
SUCCESS_TTL = timedelta(minutes=5)
PROBLEM_TTL = timedelta(days=30)

DeploymentPhase = Literal[
    "preparing",
    "restarting",
    "verifying",
    "app_healthy",
    "succeeded",
    "failed",
    "rolled_back",
]
ProjectionState = Literal["idle", "preparing", "restarting", "back_up", "problem", "unknown"]
DeploymentOutcome = Literal["succeeded", "failed", "rolled_back"]

_PHASES: frozenset[str] = frozenset(
    {
        "preparing",
        "restarting",
        "verifying",
        "app_healthy",
        "succeeded",
        "failed",
        "rolled_back",
    }
)
_NONTERMINAL_PHASES = frozenset({"preparing", "restarting", "verifying", "app_healthy"})
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DEPLOYMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "preparing": frozenset({"preparing", "restarting", "verifying", "failed"}),
    "restarting": frozenset(
        {"restarting", "verifying", "app_healthy", "failed", "rolled_back"}
    ),
    "verifying": frozenset(
        {"restarting", "verifying", "app_healthy", "failed", "rolled_back"}
    ),
    "app_healthy": frozenset({"app_healthy", "succeeded", "failed", "rolled_back"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "rolled_back": frozenset(),
}


class DeploymentLifecycleError(RuntimeError):
    """The lifecycle record could not be safely read or changed."""


class DeploymentLifecycleConflict(DeploymentLifecycleError):
    """A compare-and-swap rejected a stale deployment writer."""


@dataclass(frozen=True)
class DeploymentLifecycle:
    version: int
    deployment_id: str
    requested_sha: str
    phase: DeploymentPhase
    updated_at: datetime
    expires_at: datetime
    prior_sha: str | None = None
    serving_sha: str | None = None
    detail: str | None = None
    code: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": self.version,
            "deployment_id": self.deployment_id,
            "requested_sha": self.requested_sha,
            "phase": self.phase,
            "updated_at": _format_time(self.updated_at),
            "expires_at": _format_time(self.expires_at),
        }
        for name in ("prior_sha", "serving_sha", "detail", "code"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


@dataclass(frozen=True)
class LifecycleRead:
    available: bool
    lifecycle: DeploymentLifecycle | None = None
    reason: str | None = None

    @classmethod
    def unavailable(cls, reason: str) -> LifecycleRead:
        return cls(available=False, reason=reason)


@dataclass(frozen=True)
class LifecycleProjection:
    state: ProjectionState
    deployed_sha: str | None
    target_sha: str | None
    outcome: DeploymentOutcome | None
    detail: str | None = None
    valid_until: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


def lifecycle_path(current_root: Path) -> Path:
    """Return the one deployment record owned by a deployed Panels root."""
    return current_root / "data" / "deployment-lifecycle.json"


class DeploymentLifecycleStore:
    """Locked lifecycle transitions with deployment-id compare-and-swap."""

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = path
        self.lock_path = path.with_name(f"{path.name}.lock")
        self._now = now

    def read(self) -> LifecycleRead:
        return _read_lifecycle(self.path)

    def start(
        self,
        deployment_id: str,
        requested_sha: str,
        *,
        expected_deployment_id: str | None = None,
        prior_sha: str | None = None,
        detail: str | None = None,
        code: str | None = None,
    ) -> DeploymentLifecycle:
        _validate_deployment_id(deployment_id)
        _validate_sha(requested_sha, "requested_sha")
        _validate_optional_sha(prior_sha, "prior_sha")
        _validate_detail(detail)
        _validate_code(code)
        with _exclusive_lock(self.lock_path):
            current = _read_lifecycle(self.path)
            current_id = (
                current.lifecycle.deployment_id
                if current.available and current.lifecycle is not None
                else None
            )
            if current_id != expected_deployment_id:
                raise DeploymentLifecycleConflict(
                    f"lifecycle changed: expected {expected_deployment_id!r}, "
                    f"found {current_id!r}"
                )
            if deployment_id == current_id:
                raise DeploymentLifecycleConflict("a new deployment must use a new deployment_id")
            now = _utc_now(self._now)
            lifecycle = DeploymentLifecycle(
                version=LIFECYCLE_VERSION,
                deployment_id=deployment_id,
                requested_sha=requested_sha,
                prior_sha=prior_sha,
                phase="preparing",
                detail=detail,
                code=code,
                updated_at=now,
                expires_at=now + NONTERMINAL_TTL,
            )
            _atomic_write(self.path, lifecycle.to_public_dict())
            return lifecycle

    def transition(
        self,
        deployment_id: str,
        phase: DeploymentPhase,
        *,
        serving_sha: str | None = None,
        detail: str | None = None,
        code: str | None = None,
        preserve_terminal: bool = False,
    ) -> DeploymentLifecycle:
        _validate_deployment_id(deployment_id)
        if phase not in _PHASES:
            raise DeploymentLifecycleError("unsupported deployment phase")
        _validate_optional_sha(serving_sha, "serving_sha")
        _validate_detail(detail)
        _validate_code(code)
        with _exclusive_lock(self.lock_path):
            current_read = _read_lifecycle(self.path)
            if not current_read.available or current_read.lifecycle is None:
                raise DeploymentLifecycleConflict("there is no readable lifecycle to transition")
            current = current_read.lifecycle
            if current.deployment_id != deployment_id:
                raise DeploymentLifecycleConflict(
                    f"stale deployment {deployment_id!r}; current is "
                    f"{current.deployment_id!r}"
                )
            if preserve_terminal and current.phase in {"succeeded", "failed", "rolled_back"}:
                return current
            if phase not in _ALLOWED_TRANSITIONS[current.phase]:
                raise DeploymentLifecycleError(
                    f"invalid deployment transition: {current.phase} -> {phase}"
                )
            effective_serving_sha = serving_sha or current.serving_sha
            _validate_serving_proof(current, phase, effective_serving_sha)
            now = _utc_now(self._now)
            lifecycle = DeploymentLifecycle(
                version=LIFECYCLE_VERSION,
                deployment_id=current.deployment_id,
                requested_sha=current.requested_sha,
                prior_sha=current.prior_sha,
                serving_sha=effective_serving_sha,
                phase=phase,
                detail=detail,
                code=code,
                updated_at=now,
                expires_at=now + _ttl_for_phase(phase),
            )
            _atomic_write(self.path, lifecycle.to_public_dict())
            return lifecycle

    def project(self, deployed_sha: str | None) -> LifecycleProjection:
        _validate_optional_sha(deployed_sha, "deployed_sha")
        read = self.read()
        if not read.available or read.lifecycle is None:
            state: ProjectionState = (
                "idle"
                if read.reason == "deployment status has not been recorded"
                else "unknown"
            )
            return LifecycleProjection(
                state=state,
                deployed_sha=deployed_sha,
                target_sha=None,
                outcome=None,
                detail=None if state == "idle" else read.reason,
            )
        lifecycle = read.lifecycle
        now = _utc_now(self._now)
        valid_until = _format_time(lifecycle.expires_at)
        if lifecycle.phase in _NONTERMINAL_PHASES and now >= lifecycle.expires_at:
            return LifecycleProjection(
                state="unknown",
                deployed_sha=deployed_sha,
                target_sha=lifecycle.requested_sha,
                outcome=None,
                detail="Deployment progress expired before completion was confirmed.",
            )
        if lifecycle.phase == "preparing":
            state = "preparing"
            outcome: DeploymentOutcome | None = None
        elif lifecycle.phase in {"restarting", "verifying", "app_healthy"}:
            state = "restarting"
            outcome = None
        elif lifecycle.phase == "succeeded":
            outcome = "succeeded"
            if deployed_sha != lifecycle.requested_sha:
                state = "problem"
            elif now < lifecycle.expires_at:
                state = "back_up"
            else:
                state = "idle"
        elif lifecycle.phase == "rolled_back":
            state = "problem"
            outcome = "rolled_back"
        else:
            state = "problem"
            outcome = "failed"
        detail = lifecycle.detail
        if lifecycle.phase == "succeeded" and state == "problem":
            detail = "Recorded deployment success does not match the running app."
        return LifecycleProjection(
            state=state,
            deployed_sha=deployed_sha,
            target_sha=lifecycle.requested_sha,
            outcome=outcome,
            detail=detail,
            valid_until=(
                valid_until
                if lifecycle.phase in _NONTERMINAL_PHASES
                or (lifecycle.phase == "succeeded" and state == "back_up")
                else None
            ),
        )


def _read_lifecycle(path: Path) -> LifecycleRead:
    try:
        descriptor = _open_regular_readonly(path)
    except FileNotFoundError:
        return LifecycleRead.unavailable("deployment status has not been recorded")
    except OSError:
        return LifecycleRead.unavailable("deployment status file is unavailable")
    try:
        info = os.fstat(descriptor)
        if info.st_size > MAX_LIFECYCLE_BYTES:
            return LifecycleRead.unavailable("deployment status file is too large")
        chunks: list[bytes] = []
        remaining = MAX_LIFECYCLE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > MAX_LIFECYCLE_BYTES:
            return LifecycleRead.unavailable("deployment status file is too large")
    except OSError:
        return LifecycleRead.unavailable("deployment status file is unreadable")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(content)
        lifecycle = _parse_lifecycle(payload)
    except (DeploymentLifecycleError, UnicodeDecodeError, json.JSONDecodeError):
        return LifecycleRead.unavailable("deployment status file is malformed")
    return LifecycleRead(available=True, lifecycle=lifecycle)


def _parse_lifecycle(payload: object) -> DeploymentLifecycle:
    if not isinstance(payload, dict):
        raise DeploymentLifecycleError("lifecycle must be an object")
    version = payload.get("version")
    if type(version) is not int or version != LIFECYCLE_VERSION:
        raise DeploymentLifecycleError("unsupported lifecycle version")
    deployment_id = _required_string(payload, "deployment_id")
    requested_sha = _required_string(payload, "requested_sha")
    phase_value = _required_string(payload, "phase")
    if phase_value not in _PHASES:
        raise DeploymentLifecycleError("unsupported deployment phase")
    phase = cast(DeploymentPhase, phase_value)
    prior_sha = _optional_string(payload, "prior_sha")
    serving_sha = _optional_string(payload, "serving_sha")
    detail = _optional_string(payload, "detail")
    code = _optional_string(payload, "code")
    _validate_deployment_id(deployment_id)
    _validate_sha(requested_sha, "requested_sha")
    _validate_optional_sha(prior_sha, "prior_sha")
    _validate_optional_sha(serving_sha, "serving_sha")
    _validate_detail(detail)
    _validate_code(code)
    updated_at = _parse_time(_required_string(payload, "updated_at"))
    expires_at = _parse_time(_required_string(payload, "expires_at"))
    if expires_at <= updated_at:
        raise DeploymentLifecycleError("expires_at must follow updated_at")
    lifecycle = DeploymentLifecycle(
        version=version,
        deployment_id=deployment_id,
        requested_sha=requested_sha,
        phase=phase,
        prior_sha=prior_sha,
        serving_sha=serving_sha,
        detail=detail,
        code=code,
        updated_at=updated_at,
        expires_at=expires_at,
    )
    _validate_stored_serving_sha(lifecycle)
    return lifecycle


def _validate_stored_serving_sha(lifecycle: DeploymentLifecycle) -> None:
    if lifecycle.phase in {"app_healthy", "succeeded"}:
        if lifecycle.serving_sha != lifecycle.requested_sha:
            raise DeploymentLifecycleError("healthy deployment lacks serving SHA proof")
    if lifecycle.phase == "rolled_back":
        if lifecycle.prior_sha is None or lifecycle.serving_sha != lifecycle.prior_sha:
            raise DeploymentLifecycleError("rollback lacks prior serving SHA proof")


def _validate_serving_proof(
    current: DeploymentLifecycle,
    phase: DeploymentPhase,
    serving_sha: str | None,
) -> None:
    if phase in {"app_healthy", "succeeded"} and serving_sha != current.requested_sha:
        raise DeploymentLifecycleError("healthy phases require the requested serving SHA")
    if phase == "rolled_back" and (
        current.prior_sha is None or serving_sha != current.prior_sha
    ):
        raise DeploymentLifecycleError("rolled_back requires the prior serving SHA")


def _ttl_for_phase(phase: DeploymentPhase) -> timedelta:
    if phase == "succeeded":
        return SUCCESS_TTL
    if phase in {"failed", "rolled_back"}:
        return PROBLEM_TTL
    return NONTERMINAL_TTL


def _required_string(payload: dict[object, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise DeploymentLifecycleError(f"{name} must be a string")
    return value


def _optional_string(payload: dict[object, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DeploymentLifecycleError(f"{name} must be a string")
    return value


def _validate_deployment_id(value: str) -> None:
    if (
        not value
        or len(value) > MAX_DEPLOYMENT_ID_LENGTH
        or _DEPLOYMENT_ID_RE.fullmatch(value) is None
    ):
        raise DeploymentLifecycleError("deployment_id is invalid")


def _validate_sha(value: str, name: str) -> None:
    if _SHA_RE.fullmatch(value) is None:
        raise DeploymentLifecycleError(f"{name} must be a full lowercase Git SHA")


def _validate_optional_sha(value: str | None, name: str) -> None:
    if value is not None:
        _validate_sha(value, name)


def _validate_detail(value: str | None) -> None:
    if value is None:
        return
    if not value or len(value) > MAX_DETAIL_LENGTH or any(ord(char) < 32 for char in value):
        raise DeploymentLifecycleError("detail must be bounded printable text")


def _validate_code(value: str | None) -> None:
    if value is None:
        return
    if len(value) > MAX_CODE_LENGTH or _CODE_RE.fullmatch(value) is None:
        raise DeploymentLifecycleError("code must be a bounded public token")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DeploymentLifecycleError("invalid lifecycle timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise DeploymentLifecycleError("lifecycle timestamps must be UTC")
    return parsed.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_now(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None:
        raise DeploymentLifecycleError("injected wall clock must be timezone-aware")
    return value.astimezone(UTC)


def _open_regular_readonly(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError("lifecycle path is not a regular file")
    return descriptor


class _exclusive_lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream: TextIO | None = None

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise DeploymentLifecycleError("lifecycle lock must not be a symlink")
        descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise DeploymentLifecycleError("lifecycle lock must be a regular file")
        self.stream = os.fdopen(descriptor, "r+", encoding="utf-8")
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)

    def __exit__(self, *_: object) -> None:
        assert self.stream is not None
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DeploymentLifecycleError("lifecycle path must not be a symlink")
    if path.exists() and not path.is_file():
        raise DeploymentLifecycleError("lifecycle path must be a regular file")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_LIFECYCLE_BYTES:
        raise DeploymentLifecycleError("lifecycle record is too large")
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise DeploymentLifecycleError("could not persist deployment lifecycle") from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read or write deployment lifecycle evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--path", type=Path, required=True)
    start.add_argument("--deployment-id", required=True)
    start.add_argument("--requested-sha", required=True)
    start.add_argument("--expected-deployment-id")
    start.add_argument("--prior-sha")
    start.add_argument("--detail")
    start.add_argument("--code")

    transition = subparsers.add_parser("transition")
    transition.add_argument("--path", type=Path, required=True)
    transition.add_argument("--deployment-id", required=True)
    transition.add_argument("--phase", choices=sorted(_PHASES), required=True)
    transition.add_argument("--serving-sha")
    transition.add_argument("--detail")
    transition.add_argument("--code")
    transition.add_argument("--preserve-terminal", action="store_true")

    read = subparsers.add_parser("read")
    read.add_argument("--path", type=Path, required=True)

    project = subparsers.add_parser("project")
    project.add_argument("--path", type=Path, required=True)
    project.add_argument("--deployed-sha")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Small stdlib-only entry point for the deployment workflow and operators."""
    arguments = _cli_parser().parse_args(argv)
    store = DeploymentLifecycleStore(arguments.path)
    try:
        if arguments.command == "start":
            lifecycle = store.start(
                arguments.deployment_id,
                arguments.requested_sha,
                expected_deployment_id=arguments.expected_deployment_id,
                prior_sha=arguments.prior_sha,
                detail=arguments.detail,
                code=arguments.code,
            )
            payload: dict[str, object] = lifecycle.to_public_dict()
        elif arguments.command == "transition":
            lifecycle = store.transition(
                arguments.deployment_id,
                cast(DeploymentPhase, arguments.phase),
                serving_sha=arguments.serving_sha,
                detail=arguments.detail,
                code=arguments.code,
                preserve_terminal=arguments.preserve_terminal,
            )
            payload = lifecycle.to_public_dict()
        elif arguments.command == "read":
            read = store.read()
            payload = (
                read.lifecycle.to_public_dict()
                if read.available and read.lifecycle is not None
                else {"available": False, "reason": read.reason}
            )
        else:
            payload = store.project(arguments.deployed_sha).to_public_dict()
    except DeploymentLifecycleError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
