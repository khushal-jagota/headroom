"""Pure validation for isolated Panels environment contracts."""

from __future__ import annotations

import os
from pathlib import Path

from planner.environments.contracts import (
    EnvironmentKind,
    EnvironmentValidationError,
    FixedEnvironmentPort,
    ResolvedEnvironmentInstance,
)

MAX_AF_UNIX_SOCKET_PATH_BYTES = 103


def validate_instance_id(kind: EnvironmentKind, instance_id: str | None) -> str:
    if kind == "live":
        if instance_id not in (None, "live"):
            raise EnvironmentValidationError("live instance id must be 'live'")
        return "live"
    if kind == "staging":
        if instance_id not in (None, "staging"):
            raise EnvironmentValidationError("staging instance id must be 'staging'")
        return "staging"
    raise EnvironmentValidationError(f"unknown environment kind: {kind}")


def validate_absolute_environment_root(environment_root: Path) -> Path:
    if not environment_root.is_absolute():
        raise EnvironmentValidationError("environment root must be absolute")
    return environment_root.resolve()


def validate_repository_roots(
    requested_roots: tuple[Path, ...],
    allowed_roots: tuple[Path, ...],
) -> tuple[Path, ...]:
    resolved_allowed_roots = tuple(
        _validate_existing_repository_root(root) for root in allowed_roots
    )
    resolved_requested_roots = tuple(
        _validate_existing_repository_root(root) for root in requested_roots
    )

    allowed_set = set(resolved_allowed_roots)
    for root in resolved_requested_roots:
        if root not in allowed_set:
            raise EnvironmentValidationError(f"repository root is not allowed: {root}")
    return resolved_requested_roots


def validate_server_control_socket_path_length(path: Path) -> Path:
    encoded_length = len(os.fsencode(path))
    if encoded_length > MAX_AF_UNIX_SOCKET_PATH_BYTES:
        raise EnvironmentValidationError(
            "server control socket path is too long for AF_UNIX "
            f"({encoded_length} bytes; max {MAX_AF_UNIX_SOCKET_PATH_BYTES}): {path}"
        )
    return path


def validate_nonproduction_credential_reference_outside_live_root(
    *,
    kind: EnvironmentKind,
    environment_root: Path,
    credentials_env_file: Path | None,
) -> Path | None:
    if credentials_env_file is None:
        return None
    resolved_credentials_env_file = credentials_env_file.resolve()
    if kind == "live":
        return resolved_credentials_env_file

    live_root = validate_absolute_environment_root(environment_root) / "live"
    if (
        resolved_credentials_env_file == live_root
        or live_root in resolved_credentials_env_file.parents
    ):
        raise EnvironmentValidationError(
            "staging credential references must not be equal to "
            f"or nested under the live environment root: {resolved_credentials_env_file}"
        )
    return resolved_credentials_env_file


def validate_resolved_registry(instances: tuple[ResolvedEnvironmentInstance, ...]) -> None:
    reject_duplicate_ports(instances)
    _reject_duplicate_instance_ids(instances)

    live_instances = [instance for instance in instances if instance.kind == "live"]
    if len(live_instances) > 1:
        raise EnvironmentValidationError("only one live environment is allowed")
    live_instance = live_instances[0] if live_instances else None

    _reject_cross_instance_overlap(instances)
    if live_instance is not None:
        for instance in instances:
            if instance.kind != "live":
                reject_nonproduction_nested_under_live(instance, live_instance)


def reject_duplicate_ports(instances: tuple[ResolvedEnvironmentInstance, ...]) -> None:
    ports_by_value: dict[int, str] = {}
    for instance in instances:
        if not isinstance(instance.port_policy, FixedEnvironmentPort):
            continue
        port = instance.port_policy.port
        previous_label = ports_by_value.get(port)
        if previous_label is not None:
            raise EnvironmentValidationError(
                f"duplicate port {port}: {previous_label} and {instance.instance_id}"
            )
        ports_by_value[port] = instance.instance_id


def reject_overlapping_paths(paths_by_label: dict[str, Path]) -> None:
    resolved_paths = {
        label: _resolve_for_contract(path)
        for label, path in paths_by_label.items()
        if path is not None
    }
    labels = sorted(resolved_paths)
    for index, left_label in enumerate(labels):
        left_path = resolved_paths[left_label]
        for right_label in labels[index + 1 :]:
            right_path = resolved_paths[right_label]
            if _paths_overlap(left_path, right_path):
                raise EnvironmentValidationError(
                    f"overlapping paths: {left_label}={left_path} and {right_label}={right_path}"
                )


def reject_nonproduction_nested_under_live(
    candidate: ResolvedEnvironmentInstance,
    live: ResolvedEnvironmentInstance,
) -> None:
    live_paths = _isolation_paths(live)
    for candidate_label, candidate_path in _isolation_paths(candidate).items():
        for live_label, live_path in live_paths.items():
            if _paths_overlap(candidate_path, live_path):
                raise EnvironmentValidationError(
                    "nonproduction path overlaps live path: "
                    f"{candidate_label}={candidate_path} and {live_label}={live_path}"
                )


def _reject_duplicate_instance_ids(instances: tuple[ResolvedEnvironmentInstance, ...]) -> None:
    ids_seen: set[str] = set()
    for instance in instances:
        if instance.instance_id in ids_seen:
            raise EnvironmentValidationError(f"duplicate instance id: {instance.instance_id}")
        ids_seen.add(instance.instance_id)


def _reject_cross_instance_overlap(instances: tuple[ResolvedEnvironmentInstance, ...]) -> None:
    for index, left_instance in enumerate(instances):
        left_paths = _isolation_paths(left_instance)
        for right_instance in instances[index + 1 :]:
            right_paths = _isolation_paths(right_instance)
            for left_label, left_path in left_paths.items():
                for right_label, right_path in right_paths.items():
                    if _paths_overlap(left_path, right_path):
                        raise EnvironmentValidationError(
                            "overlapping paths: "
                            f"{left_instance.instance_id}.{left_label}={left_path} and "
                            f"{right_instance.instance_id}.{right_label}={right_path}"
                        )


def _isolation_paths(instance: ResolvedEnvironmentInstance) -> dict[str, Path]:
    paths = {
        "instance_root": instance.instance_root,
        "db_path": instance.db_path,
        "managed_files_root": instance.managed_files_root,
        "hermes_home": instance.hermes_home,
        "runtime_user_home": instance.runtime_user_home,
        "logs_dir": instance.logs_dir,
        "dispatcher_lock_path": instance.dispatcher_lock_path,
        "server_control_socket_path": instance.server_control_socket_path,
    }
    if instance.credentials_env_file is not None:
        paths["credentials_env_file"] = instance.credentials_env_file
    for index, repository_root in enumerate(instance.allowed_repository_roots):
        paths[f"repository_roots[{index}]"] = repository_root
    return paths


def _validate_existing_repository_root(root: Path) -> Path:
    resolved_root = root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise EnvironmentValidationError(f"repository root does not exist: {resolved_root}")
    git_marker = resolved_root / ".git"
    if not _is_git_worktree_marker(git_marker):
        raise EnvironmentValidationError(f"repository root is not a worktree: {resolved_root}")
    return resolved_root


def _is_git_worktree_marker(git_marker: Path) -> bool:
    if git_marker.is_dir():
        return (git_marker / "HEAD").is_file() and (git_marker / "objects").is_dir()
    if not git_marker.is_file():
        return False
    try:
        prefix, raw_git_directory = git_marker.read_text(encoding="utf-8").strip().split(":", 1)
    except (OSError, ValueError):
        return False
    if prefix != "gitdir":
        return False
    git_directory = Path(raw_git_directory.strip())
    if not git_directory.is_absolute():
        git_directory = git_marker.parent / git_directory
    return git_directory.resolve().is_dir() and (git_directory.resolve() / "HEAD").is_file()


def _resolve_for_contract(path: Path) -> Path:
    return path.resolve()


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents
