"""Resolution helpers for isolated Panels environment instances."""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path

from planner.environments.contracts import (
    EnvironmentDefaults,
    EnvironmentKind,
    EnvironmentValidationError,
    ResolvedEnvironmentInstance,
    validate_tcp_port,
)
from planner.environments.logic.validation import (
    validate_absolute_environment_root,
    validate_instance_id,
    validate_nonproduction_credential_reference_outside_live_root,
    validate_repository_roots,
    validate_server_control_socket_path_length,
)

REGISTRY_LOCK_FILENAME = ".registry.lock"


class EnvironmentRegistryLock:
    """Retained advisory lock for one environment root's manifests."""

    def __init__(self, environment_root: Path) -> None:
        self._environment_root = validate_absolute_environment_root(environment_root)
        self._path = self._environment_root / REGISTRY_LOCK_FILENAME
        self._descriptor: int | None = None

    def acquire(self) -> None:
        self._environment_root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise EnvironmentValidationError(
                    f"environment registry is locked: {self._environment_root}"
                ) from exc
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = None

    def __enter__(self) -> EnvironmentRegistryLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def acquire_environment_registry_lock(environment_root: Path) -> EnvironmentRegistryLock:
    return EnvironmentRegistryLock(environment_root)


def allocate_preview_port(
    *,
    used_ports: set[int],
    defaults: EnvironmentDefaults,
) -> int:
    for port in range(defaults.preview_ports.start, defaults.preview_ports.end + 1):
        if port not in used_ports:
            return port
    raise EnvironmentValidationError("no preview ports are available")


def resolve_environment_instance(
    *,
    kind: EnvironmentKind,
    environment_root: Path,
    instance_id: str | None = None,
    port: int | None = None,
    credentials_env_file: Path | None = None,
    allowed_repository_roots: tuple[Path, ...] = (),
    requested_repository_roots: tuple[Path, ...] = (),
    defaults: EnvironmentDefaults | None = None,
    fixture_version: str | None = None,
    prepared: bool = False,
    running: bool = False,
) -> ResolvedEnvironmentInstance:
    resolved_instance_id = validate_instance_id(kind, instance_id)
    resolved_environment_root = validate_absolute_environment_root(environment_root)
    resolved_repository_roots = validate_repository_roots(
        requested_repository_roots,
        allowed_repository_roots,
    )
    resolved_defaults = defaults if defaults is not None else EnvironmentDefaults()

    instance_root = _default_instance_root(
        kind=kind,
        instance_id=resolved_instance_id,
        environment_root=resolved_environment_root,
    )
    resolved_port = _resolve_port(kind=kind, port=port, defaults=resolved_defaults)
    resolved_credentials_env_file = validate_nonproduction_credential_reference_outside_live_root(
        kind=kind,
        environment_root=resolved_environment_root,
        credentials_env_file=credentials_env_file,
    )
    server_control_socket_path = instance_root / "run" / "server-control.sock"
    validate_server_control_socket_path_length(server_control_socket_path)

    return ResolvedEnvironmentInstance(
        kind=kind,
        instance_id=resolved_instance_id,
        environment_root=resolved_environment_root,
        instance_root=instance_root,
        db_path=instance_root / "data" / "planner.db",
        managed_files_root=instance_root / "data" / "files",
        hermes_home=instance_root / "hermes-home",
        logs_dir=instance_root / "logs",
        dispatcher_lock_path=instance_root / "run" / "dispatcher.lock",
        server_control_socket_path=server_control_socket_path,
        port=resolved_port,
        credentials_env_file=resolved_credentials_env_file,
        allowed_repository_roots=resolved_repository_roots,
        expected_linux_account="panels-live" if kind == "live" else "panels-worker",
        fixture_version=fixture_version,
        prepared=prepared,
        running=running,
    )


def _default_instance_root(
    *,
    kind: EnvironmentKind,
    instance_id: str,
    environment_root: Path,
) -> Path:
    if kind == "live":
        return environment_root / "live"
    if kind == "staging":
        return environment_root / "staging"
    if kind == "preview":
        return environment_root / "previews" / instance_id
    raise EnvironmentValidationError(f"unknown environment kind: {kind}")


def _resolve_port(
    *,
    kind: EnvironmentKind,
    port: int | None,
    defaults: EnvironmentDefaults,
) -> int:
    if port is not None:
        return validate_tcp_port(port)
    if kind == "live":
        return validate_tcp_port(defaults.live_port, label="live default port")
    if kind == "staging":
        return validate_tcp_port(defaults.staging_port, label="staging default port")
    raise EnvironmentValidationError("preview port must be allocated by the caller")
