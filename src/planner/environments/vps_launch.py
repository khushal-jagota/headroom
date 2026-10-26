"""Explicit owner-controlled VPS executor mode; never read from ambient flags."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Literal, cast

from planner.environments.executor_launch_contracts import (
    ExecutorLaunchConfigurationError,
    VpsExecutorLaunchConfiguration,
)

MAX_VPS_LAUNCH_CONFIGURATION_BYTES = 4096


def validate_vps_launch(configuration: VpsExecutorLaunchConfiguration) -> None:
    if type(configuration.version) is not int or configuration.version not in {1, 2}:
        raise ExecutorLaunchConfigurationError(
            "unsupported VPS executor launch configuration version"
        )
    if configuration.mode not in {"local", "remote"}:
        raise ExecutorLaunchConfigurationError("unsupported VPS executor launch mode")
    if configuration.version == 1:
        path = configuration.socket_path
        if (
            configuration.executor_id is not None
            or (configuration.mode == "local" and path is not None)
            or (
                configuration.mode == "remote"
                and (
                    not isinstance(path, Path)
                    or not path.is_absolute()
                    or any(ord(char) < 32 for char in str(path))
                    or ".." in path.parts
                )
            )
        ):
            raise ExecutorLaunchConfigurationError(
                "invalid legacy VPS executor socket configuration"
            )
        return
    if configuration.socket_path is not None:
        raise ExecutorLaunchConfigurationError("version 2 cannot configure an executor socket")
    if configuration.mode == "local" and configuration.executor_id is not None:
        raise ExecutorLaunchConfigurationError("local executor launch cannot set an executor ID")
    if configuration.mode == "remote" and (
        not isinstance(configuration.executor_id, str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", configuration.executor_id) is None
    ):
        raise ExecutorLaunchConfigurationError("remote VPS executor launch requires an executor ID")


def validate_vps_launch_for_application(configuration: VpsExecutorLaunchConfiguration) -> None:
    validate_vps_launch(configuration)
    if configuration.version == 1 and configuration.mode == "remote":
        raise ExecutorLaunchConfigurationError(
            "legacy remote launch requires an explicitly prepared version-2 executor ID descriptor"
        )


def _safe_path(path: Path) -> None:
    if not path.is_absolute():
        raise ExecutorLaunchConfigurationError(
            "VPS executor launch configuration path must be absolute"
        )
    if any(component.is_symlink() for component in (path, *path.parents)):
        raise ExecutorLaunchConfigurationError(
            "VPS executor launch configuration path contains a symbolic link"
        )
    if path.parent.exists():
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
            raise ExecutorLaunchConfigurationError(
                "VPS executor launch directory must be owner controlled"
            )


def validate_vps_launch_path(path: Path) -> None:
    """Check an explicit installation target before disrupting its service."""
    _safe_path(path)
    _read_bytes(path)


def _read_bytes(path: Path) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        # An absent explicit configuration preserves the existing local default,
        # including older installations whose data directory is shared.
        if not path.is_absolute() or any(item.is_symlink() for item in path.parents):
            raise ExecutorLaunchConfigurationError("unsafe VPS executor launch configuration path")
        return None
    _safe_path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ExecutorLaunchConfigurationError(
                "VPS executor launch configuration must be an owner-only regular file"
            )
        value = stream.read(MAX_VPS_LAUNCH_CONFIGURATION_BYTES + 1)
    if len(value) > MAX_VPS_LAUNCH_CONFIGURATION_BYTES:
        raise ExecutorLaunchConfigurationError(
            "VPS executor launch configuration exceeds its size limit"
        )
    return value


def _decode(raw: bytes) -> VpsExecutorLaunchConfiguration:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("configuration must be an object")
        version = value.get("version")
        if type(version) is not int:
            raise ValueError("configuration version must be an integer")
        field = "socket" if version == 1 else "executor_id"
        if set(value) != {"version", "mode", field}:
            raise ValueError("configuration fields do not match the supported schema")
        mode, target = value["mode"], value[field]
        if mode not in ("local", "remote") or (target is not None and not isinstance(target, str)):
            raise ValueError("configuration fields have invalid types")
        configuration = VpsExecutorLaunchConfiguration(
            mode=cast(Literal["local", "remote"], mode),
            version=version,
            executor_id=target if version != 1 else None,
            socket_path=Path(target) if version == 1 and target is not None else None,
        )
        validate_vps_launch(configuration)
        return configuration
    except (ValueError, TypeError) as exc:
        raise ExecutorLaunchConfigurationError(
            "malformed VPS executor launch configuration"
        ) from exc


def read_vps_launch(path: Path) -> VpsExecutorLaunchConfiguration | None:
    raw = snapshot_vps_launch(path)
    return _decode(raw) if raw is not None else None


def snapshot_vps_launch(path: Path) -> bytes | None:
    raw = _read_bytes(path)
    if raw is not None:
        _decode(raw)
    return raw


def restore_vps_launch(path: Path, raw: bytes | None) -> None:
    """Restore exact validated prior bytes, or the prior absence, under deploy lock."""
    if raw is not None:
        if len(raw) > MAX_VPS_LAUNCH_CONFIGURATION_BYTES:
            raise ExecutorLaunchConfigurationError(
                "VPS executor launch configuration exceeds its size limit"
            )
        _decode(raw)
        _write_bytes(path, raw)
        return
    _safe_path(path)
    if _read_bytes(path) is not None:
        path.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def write_vps_launch(path: Path, configuration: VpsExecutorLaunchConfiguration) -> None:
    """Atomically replace only a validated owner-controlled configuration file."""
    validate_vps_launch(configuration)
    encoded = (
        json.dumps(
            {
                "version": configuration.version,
                "mode": configuration.mode,
                **(
                    {
                        "socket": str(configuration.socket_path)
                        if configuration.socket_path
                        else None
                    }
                    if configuration.version == 1
                    else {"executor_id": configuration.executor_id}
                ),
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    _write_bytes(path, encoded)


def _write_bytes(path: Path, encoded: bytes) -> None:
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_path(path)
    _read_bytes(path)
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".executor-launch-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _safe_path(path)
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
