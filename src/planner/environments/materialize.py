"""Filesystem materialization for isolated Panels runtime environments."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from planner.conversation.hermes_backend_configuration import provision_planner_home_skills
from planner.environments.contracts import (
    DynamicEnvironmentPort,
    EnvironmentDefaults,
    EnvironmentKind,
    EnvironmentManifest,
    EnvironmentValidationError,
    FixedEnvironmentPort,
    ResolvedEnvironmentInstance,
)
from planner.environments.fake_fixture import (
    FAKE_FIXTURE_VERSION,
    build_fake_environment_database,
)
from planner.environments.logic.credentials import parse_environment_file
from planner.environments.logic.registry import (
    acquire_environment_registry_lock,
    resolve_environment_instance,
)
from planner.environments.logic.validation import (
    reject_overlapping_paths,
    validate_absolute_environment_root,
    validate_instance_id,
    validate_nonproduction_credential_reference_outside_live_root,
    validate_repository_roots,
    validate_resolved_registry,
)
from planner.server_lifecycle.contracts import (
    ServerLifecycleAlreadyOwnedError,
    ServerLifecycleError,
)
from planner.server_lifecycle.control import resolve_server_lifecycle_lease_path
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease

MANIFEST_FILENAME = "manifest.json"


def prepare_environment_instance(
    *,
    kind: EnvironmentKind,
    environment_root: Path,
    instance_id: str | None = None,
    port: int | None = None,
    credentials_env_file: Path | None = None,
    repository_roots: tuple[Path, ...],
    defaults: EnvironmentDefaults | None = None,
    now: int | None = None,
) -> EnvironmentManifest:
    effective_now = _default_prepare_now(kind, now)
    resolved_environment_root = validate_absolute_environment_root(environment_root)
    resolved_instance_id = validate_instance_id(kind, instance_id)
    resolved_defaults = defaults if defaults is not None else EnvironmentDefaults()
    validate_nonproduction_credential_reference_outside_live_root(
        kind=kind,
        environment_root=resolved_environment_root,
        credentials_env_file=credentials_env_file,
    )
    _validate_existing_credentials_file(credentials_env_file, kind=kind)
    with acquire_environment_registry_lock(resolved_environment_root):
        registry_manifests = _read_registry_manifests(resolved_environment_root)
        _validate_registry_manifests(registry_manifests)
        existing = _find_manifest(
            registry_manifests,
            kind=kind,
            instance_id=resolved_instance_id,
        )
        if existing is not None:
            _raise_if_reprepare_mismatches_existing_manifest(
                existing,
                port=port,
                credentials_env_file=credentials_env_file,
                repository_roots=repository_roots,
            )
            return existing

        fixture_version = None if kind == "live" else FAKE_FIXTURE_VERSION
        instance = resolve_environment_instance(
            kind=kind,
            instance_id=resolved_instance_id,
            environment_root=resolved_environment_root,
            port=port,
            credentials_env_file=credentials_env_file,
            allowed_repository_roots=repository_roots,
            requested_repository_roots=repository_roots,
            defaults=resolved_defaults,
            fixture_version=fixture_version,
            prepared=True,
        )
        manifest = _manifest_from_instance(instance, prepared_at=effective_now)
        _validate_registry_manifests((*registry_manifests, manifest))
        if kind == "live":
            _prepare_empty_live_generation(instance)
        _prepare_common_layout(instance)
        if kind != "live":
            _replace_data_tree_from_fixture(instance.db_path, now=effective_now)
        else:
            instance.managed_files_root.mkdir(parents=True, exist_ok=True)
        _materialize_instance_skills(instance)
        _write_manifest(manifest)
        return manifest


def reset_environment_instance(
    *,
    kind: EnvironmentKind,
    environment_root: Path,
    instance_id: str | None = None,
    port: int | None = None,
    credentials_env_file: Path | None = None,
    repository_roots: tuple[Path, ...],
    now: int = 1_800_000_000,
) -> EnvironmentManifest:
    if kind == "live":
        raise EnvironmentValidationError("live environment cannot be reset")
    resolved_environment_root = validate_absolute_environment_root(environment_root)
    with acquire_environment_registry_lock(resolved_environment_root):
        current = inspect_environment_instance(
            kind=kind,
            instance_id=instance_id,
            environment_root=resolved_environment_root,
            port=port,
            credentials_env_file=credentials_env_file,
            repository_roots=repository_roots,
        )
        if current.prepared_at is None:
            raise EnvironmentValidationError("environment instance is not prepared")
        instance = resolve_environment_instance(
            kind=current.kind,
            instance_id=current.instance_id,
            environment_root=current.environment_root,
            port=_fixed_port_or_none(current),
            credentials_env_file=current.credentials_env_file,
            allowed_repository_roots=current.repository_roots,
            requested_repository_roots=current.repository_roots,
            fixture_version=FAKE_FIXTURE_VERSION,
            prepared=True,
        )
        with _stopped_environment_lifecycle_lease(current):
            _prepare_common_layout(instance)
            _replace_data_tree_from_fixture(instance.db_path, now=now)
            _materialize_instance_skills(instance)
            manifest = _manifest_from_instance(instance, prepared_at=now)
            _write_manifest(manifest)
            return manifest


def remove_environment_instance(
    *,
    kind: EnvironmentKind,
    environment_root: Path,
    instance_id: str | None = None,
    port: int | None = None,
    credentials_env_file: Path | None = None,
    repository_roots: tuple[Path, ...],
) -> EnvironmentManifest:
    if kind == "live":
        raise EnvironmentValidationError("live environment cannot be removed")
    resolved_environment_root = validate_absolute_environment_root(environment_root)
    with acquire_environment_registry_lock(resolved_environment_root):
        current = inspect_environment_instance(
            kind=kind,
            instance_id=instance_id,
            environment_root=resolved_environment_root,
            port=port,
            credentials_env_file=credentials_env_file,
            repository_roots=repository_roots,
        )
        if current.prepared_at is None:
            raise EnvironmentValidationError("environment instance is not prepared")
        with _stopped_environment_lifecycle_lease(current):
            shutil.rmtree(current.instance_root)
        return current


def import_live_environment_state(
    *,
    environment_root: Path,
    source_db_path: Path,
    source_managed_files_root: Path,
    source_hermes_home: Path,
    source_runtime_user_home: Path,
    source_logs_root: Path,
) -> EnvironmentManifest:
    """Atomically replace a prepared, stopped live environment's durable state."""
    live = inspect_environment_instance(
        kind="live",
        environment_root=environment_root,
        repository_roots=(),
    )
    if live.prepared_at is None:
        raise EnvironmentValidationError("live environment is not prepared")
    sources = _validate_live_import_sources(
        live,
        source_db_path=source_db_path,
        source_managed_files_root=source_managed_files_root,
        source_hermes_home=source_hermes_home,
        source_runtime_user_home=source_runtime_user_home,
        source_logs_root=source_logs_root,
    )
    (
        source_db,
        source_files,
        source_hermes,
        source_worker_settings,
        source_agent_homes,
        source_logs,
    ) = sources

    with _stopped_environment_lifecycle_lease(live):
        generations_root = live.instance_root / "generations"
        generations_root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix="generation-", dir=str(generations_root)))
        committed = False
        try:
            staged_data = temporary_root / "data"
            staged_data.mkdir()
            _sqlite_backup(source_db, staged_data / "planner.db")
            shutil.copytree(source_files, staged_data / "files")
            shutil.copytree(source_worker_settings, staged_data / "worker-settings")
            staged_hermes = temporary_root / "hermes-home"
            shutil.copytree(source_hermes, staged_hermes, symlinks=True)
            staged_runtime_user_home = temporary_root / "user-home"
            staged_runtime_user_home.mkdir(mode=0o700)
            for agent_home in source_agent_homes:
                _copy_durable_tree(agent_home, staged_runtime_user_home / agent_home.name)
            staged_runtime_user_home.chmod(0o700)
            staged_logs_root = temporary_root / "logs"
            (staged_logs_root / "active").mkdir(parents=True)
            shutil.copytree(source_logs, staged_logs_root / "archive" / "pre-cutover")
            provision_planner_home_skills(
                staged_hermes,
                configured_database_parent=staged_data,
                panels_skills_source_root=_repository_skill_root(live.repository_roots[0]),
            )
            _commit_live_generation(live, temporary_root)
            committed = True
        except EnvironmentValidationError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise EnvironmentValidationError(f"live state import failed: {exc}") from exc
        finally:
            if not committed:
                shutil.rmtree(temporary_root, ignore_errors=True)
    return live


def inspect_environment_instance(
    *,
    kind: EnvironmentKind,
    environment_root: Path,
    instance_id: str | None = None,
    port: int | None = None,
    credentials_env_file: Path | None = None,
    repository_roots: tuple[Path, ...],
) -> EnvironmentManifest:
    manifest_path = _prepared_manifest_path(
        kind=kind,
        environment_root=environment_root,
        instance_id=instance_id,
    )
    if manifest_path is not None and manifest_path.exists():
        resolved_environment_root = validate_absolute_environment_root(environment_root)
        manifest = _read_manifest(
            manifest_path,
            caller_environment_root=resolved_environment_root,
        )
        if repository_roots:
            validate_repository_roots(repository_roots, manifest.repository_roots)
        if port is not None and port != _fixed_port_or_none(manifest):
            raise EnvironmentValidationError("prepared environment port does not match request")
        requested_credentials_env_file = (
            credentials_env_file.resolve() if credentials_env_file is not None else None
        )
        if (
            requested_credentials_env_file is not None
            and requested_credentials_env_file != manifest.credentials_env_file
        ):
            raise EnvironmentValidationError(
                "prepared environment credential file does not match request"
            )
        return manifest

    instance = resolve_environment_instance(
        kind=kind,
        instance_id=instance_id,
        environment_root=environment_root,
        port=port,
        credentials_env_file=credentials_env_file,
        allowed_repository_roots=repository_roots,
        requested_repository_roots=repository_roots,
    )
    manifest_path = _manifest_path(instance.instance_root)
    if not manifest_path.exists():
        return _manifest_from_instance(instance, prepared_at=None)
    return _read_manifest(
        manifest_path,
        caller_environment_root=validate_absolute_environment_root(environment_root),
    )


def manifest_to_json_dict(
    manifest: EnvironmentManifest,
    *,
    prepared: bool | None = None,
    running: bool | None = None,
) -> dict[str, Any]:
    prepared_value = manifest.prepared_at is not None if prepared is None else prepared
    payload: dict[str, Any] = {
        "kind": manifest.kind,
        "instance_id": manifest.instance_id,
        "environment_root": str(manifest.environment_root),
        "instance_root": str(manifest.instance_root),
        "db_path": str(manifest.db_path),
        "managed_files_root": str(manifest.managed_files_root),
        "hermes_home": str(manifest.hermes_home),
        "runtime_user_home": str(manifest.runtime_user_home),
        "logs_dir": str(manifest.logs_dir),
        "dispatcher_lock_path": str(manifest.dispatcher_lock_path),
        "server_control_socket_path": str(manifest.server_control_socket_path),
        "credentials_env_file": (
            str(manifest.credentials_env_file)
            if manifest.credentials_env_file is not None
            else None
        ),
        "repository_roots": [str(root) for root in manifest.repository_roots],
        "expected_linux_account": manifest.expected_linux_account,
        "fixture_version": manifest.fixture_version,
        "prepared": prepared_value,
        "prepared_at": manifest.prepared_at,
    }
    if isinstance(manifest.port_policy, FixedEnvironmentPort):
        payload["runtime_port_policy"] = "fixed"
        payload["port"] = manifest.port_policy.port
        payload["running"] = (
            _environment_lifecycle_lease_is_held(manifest) if running is None else running
        )
    else:
        payload["runtime_port_policy"] = "dynamic"
        payload["bind_attempts"] = manifest.port_policy.bind_attempts
    return payload


def manifest_to_json(
    manifest: EnvironmentManifest,
    *,
    prepared: bool | None = None,
    running: bool | None = None,
) -> str:
    return json.dumps(
        manifest_to_json_dict(manifest, prepared=prepared, running=running),
        sort_keys=True,
    )


def _replace_data_tree_from_fixture(db_path: Path, *, now: int) -> None:
    data_root = db_path.parent
    instance_root = data_root.parent
    instance_root.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=f".{data_root.name}-", dir=str(instance_root)))
    backup_root: Path | None = None
    try:
        temporary_data_root = temporary_root / data_root.name
        temporary_db_path = temporary_data_root / db_path.name
        build_fake_environment_database(temporary_db_path, now=now)
        if not temporary_db_path.is_file():
            raise EnvironmentValidationError("fake fixture did not create a database")
        if data_root.exists():
            backup_root = Path(
                tempfile.mkdtemp(prefix=f".{data_root.name}-backup-", dir=str(instance_root))
            )
            backup_data_root = backup_root / data_root.name
            os.replace(data_root, backup_data_root)
        try:
            os.replace(temporary_data_root, data_root)
        except BaseException:
            if backup_root is not None:
                backup_data_root = backup_root / data_root.name
                if backup_data_root.exists() and not data_root.exists():
                    os.replace(backup_data_root, data_root)
            raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
        if backup_root is not None:
            shutil.rmtree(backup_root, ignore_errors=True)


def _validate_live_import_sources(
    live: EnvironmentManifest,
    *,
    source_db_path: Path,
    source_managed_files_root: Path,
    source_hermes_home: Path,
    source_runtime_user_home: Path,
    source_logs_root: Path,
) -> tuple[Path, Path, Path, Path, tuple[Path, Path], Path]:
    source_db = source_db_path.resolve()
    source_files = source_managed_files_root.resolve()
    source_hermes = source_hermes_home.resolve()
    source_user_home = source_runtime_user_home.resolve()
    source_logs = source_logs_root.resolve()
    source_worker_settings = source_db.parent / "worker-settings"
    if not source_db.is_file():
        raise EnvironmentValidationError(f"live import database is not a file: {source_db}")
    for label, source in (
        ("managed files", source_files),
        ("Hermes home", source_hermes),
        ("worker settings", source_worker_settings),
        ("runtime user home", source_user_home),
        ("logs", source_logs),
    ):
        if not source.is_dir():
            raise EnvironmentValidationError(f"live import {label} is not a directory: {source}")
    source_codex_home = (source_user_home / ".codex").resolve()
    source_claude_home = (source_user_home / ".claude").resolve()
    for label, source in (
        ("Codex home", source_codex_home),
        ("Claude home", source_claude_home),
    ):
        if not source.is_dir():
            raise EnvironmentValidationError(f"live import {label} is not a directory: {source}")
    for source in (
        source_db,
        source_files,
        source_hermes,
        source_worker_settings,
        source_codex_home,
        source_claude_home,
        source_logs,
    ):
        if source == live.instance_root or live.instance_root in source.parents:
            raise EnvironmentValidationError(
                f"live import source must be outside the prepared live environment: {source}"
            )
    reject_overlapping_paths(
        {
            "source_db_path": source_db,
            "source_managed_files_root": source_files,
            "source_hermes_home": source_hermes,
            "source_worker_settings": source_worker_settings,
            "source_codex_home": source_codex_home,
            "source_claude_home": source_claude_home,
            "source_logs_root": source_logs,
        }
    )
    return (
        source_db,
        source_files,
        source_hermes,
        source_worker_settings,
        (source_codex_home, source_claude_home),
        source_logs,
    )


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_uri = f"{source.as_uri()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def _copy_durable_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=_ignore_transient_special_files,
    )
    destination.chmod(0o700)


def _ignore_transient_special_files(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    root = Path(directory)
    for name in names:
        mode = (root / name).lstat().st_mode
        if stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
            ignored.add(name)
    return ignored


def _prepare_empty_live_generation(instance: ResolvedEnvironmentInstance) -> None:
    generations_root = instance.instance_root / "generations"
    generations_root.mkdir(parents=True, exist_ok=True)
    generation = Path(tempfile.mkdtemp(prefix="generation-", dir=str(generations_root)))
    _commit_live_generation(_manifest_from_instance(instance, prepared_at=None), generation)


def _commit_live_generation(live: EnvironmentManifest, generation: Path) -> None:
    """Durably stage one generation, then atomically change the stable pointer."""
    generations_root = (live.instance_root / "generations").resolve()
    resolved_generation = generation.resolve()
    if generations_root not in resolved_generation.parents:
        raise EnvironmentValidationError("live generation must be inside its generations root")
    _fsync_tree(resolved_generation)
    current = live.instance_root / "current"
    previous_generation = current.resolve() if current.is_symlink() else None
    temporary_link = live.instance_root / f".current-{resolved_generation.name}"
    temporary_link.unlink(missing_ok=True)
    temporary_link.symlink_to(resolved_generation.relative_to(live.instance_root.resolve()))
    os.replace(temporary_link, current)
    _fsync_directory(live.instance_root)
    if (
        previous_generation is not None
        and previous_generation != resolved_generation
        and generations_root in previous_generation.parents
    ):
        try:
            shutil.rmtree(previous_generation, ignore_errors=True)
        except OSError:
            # The new generation is already committed; stale cleanup is best effort.
            pass


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda candidate: len(candidate.parts), reverse=True):
        if path.is_symlink():
            continue
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        elif path.is_dir():
            _fsync_directory(path)
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_common_layout(instance) -> None:  # type: ignore[no-untyped-def]
    instance.instance_root.mkdir(mode=0o750, parents=True, exist_ok=True)
    instance.logs_dir.mkdir(parents=True, exist_ok=True)
    instance.dispatcher_lock_path.parent.mkdir(parents=True, exist_ok=True)
    instance.server_control_socket_path.parent.mkdir(parents=True, exist_ok=True)
    instance.runtime_user_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    instance.runtime_user_home.chmod(0o700)


def _materialize_instance_skills(instance) -> None:  # type: ignore[no-untyped-def]
    provision_planner_home_skills(
        instance.hermes_home,
        configured_database_parent=instance.db_path.parent,
        panels_skills_source_root=_repository_skill_root(instance.allowed_repository_roots[0]),
    )


def _repository_skill_root(repository_root: Path) -> Path:
    source_root = repository_root / "src" / "planner" / "skills"
    if not source_root.is_dir():
        raise EnvironmentValidationError(
            f"repository has no Panels skill source root: {source_root}"
        )
    return source_root.resolve()


def _manifest_from_instance(instance, *, prepared_at: int | None) -> EnvironmentManifest:  # type: ignore[no-untyped-def]
    return EnvironmentManifest(
        kind=instance.kind,
        instance_id=instance.instance_id,
        environment_root=instance.environment_root,
        instance_root=instance.instance_root,
        db_path=instance.db_path,
        managed_files_root=instance.managed_files_root,
        hermes_home=instance.hermes_home,
        runtime_user_home=instance.runtime_user_home,
        logs_dir=instance.logs_dir,
        dispatcher_lock_path=instance.dispatcher_lock_path,
        server_control_socket_path=instance.server_control_socket_path,
        port_policy=instance.port_policy,
        credentials_env_file=instance.credentials_env_file,
        expected_linux_account=instance.expected_linux_account,
        fixture_version=instance.fixture_version,
        prepared_at=prepared_at,
        repository_roots=instance.allowed_repository_roots,
    )


def _manifest_path(instance_root: Path) -> Path:
    return instance_root / MANIFEST_FILENAME


def _prepared_manifest_path(
    *,
    kind: EnvironmentKind,
    environment_root: Path,
    instance_id: str | None,
) -> Path | None:
    resolved_environment_root = validate_absolute_environment_root(environment_root)
    validate_instance_id(kind, instance_id)
    if kind == "live":
        return resolved_environment_root / "live" / MANIFEST_FILENAME
    if kind == "staging":
        return resolved_environment_root / "staging" / MANIFEST_FILENAME
    return None


def _write_manifest(manifest: EnvironmentManifest) -> None:
    manifest.instance_root.mkdir(parents=True, exist_ok=True)
    payload = _manifest_payload(manifest)
    target = _manifest_path(manifest.instance_root)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{MANIFEST_FILENAME}-",
        dir=str(manifest.instance_root),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
            json.dump(payload, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
        os.replace(temporary_name, target)
    finally:
        if Path(temporary_name).exists():
            Path(temporary_name).unlink()


def _read_manifest(
    path: Path,
    *,
    caller_environment_root: Path | None = None,
) -> EnvironmentManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = EnvironmentManifest(
            kind=payload["kind"],
            instance_id=payload["instance_id"],
            environment_root=Path(payload["environment_root"]),
            instance_root=Path(payload["instance_root"]),
            db_path=Path(payload["db_path"]),
            managed_files_root=Path(payload["managed_files_root"]),
            hermes_home=Path(payload["hermes_home"]),
            runtime_user_home=Path(
                payload.get(
                    "runtime_user_home",
                    Path(payload["instance_root"])
                    / ("current/user-home" if payload["kind"] == "live" else "user-home"),
                )
            ),
            logs_dir=Path(payload["logs_dir"]),
            dispatcher_lock_path=Path(payload["dispatcher_lock_path"]),
            server_control_socket_path=Path(payload["server_control_socket_path"]),
            port_policy=_port_policy_from_payload(payload),
            credentials_env_file=(
                Path(payload["credentials_env_file"])
                if payload.get("credentials_env_file") is not None
                else None
            ),
            expected_linux_account=payload["expected_linux_account"],
            fixture_version=payload.get("fixture_version"),
            prepared_at=payload.get("prepared_at"),
            repository_roots=tuple(Path(root) for root in payload["repository_roots"]),
        )
    except EnvironmentValidationError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EnvironmentValidationError(f"invalid environment manifest: {path}") from exc
    if caller_environment_root is not None:
        _validate_manifest_against_location(
            manifest,
            manifest_path=path,
            caller_environment_root=caller_environment_root,
        )
    return manifest


def _manifest_payload(manifest: EnvironmentManifest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": manifest.kind,
        "instance_id": manifest.instance_id,
        "environment_root": str(manifest.environment_root),
        "instance_root": str(manifest.instance_root),
        "db_path": str(manifest.db_path),
        "managed_files_root": str(manifest.managed_files_root),
        "hermes_home": str(manifest.hermes_home),
        "runtime_user_home": str(manifest.runtime_user_home),
        "logs_dir": str(manifest.logs_dir),
        "dispatcher_lock_path": str(manifest.dispatcher_lock_path),
        "server_control_socket_path": str(manifest.server_control_socket_path),
        "runtime_port": (
            {"kind": "fixed", "port": manifest.port_policy.port}
            if isinstance(manifest.port_policy, FixedEnvironmentPort)
            else {
                "kind": "dynamic",
                "bind_attempts": manifest.port_policy.bind_attempts,
            }
        ),
        "credentials_env_file": (
            str(manifest.credentials_env_file)
            if manifest.credentials_env_file is not None
            else None
        ),
        "expected_linux_account": manifest.expected_linux_account,
        "fixture_version": manifest.fixture_version,
        "prepared_at": manifest.prepared_at,
        "repository_roots": [str(root) for root in manifest.repository_roots],
    }
    return payload


def _validate_existing_credentials_file(
    credentials_env_file: Path | None,
    *,
    kind: EnvironmentKind,
) -> None:
    if credentials_env_file is not None and credentials_env_file.exists():
        parse_environment_file(credentials_env_file, kind=kind)


def _validate_manifest_against_location(
    manifest: EnvironmentManifest,
    *,
    manifest_path: Path,
    caller_environment_root: Path,
) -> None:
    resolved_environment_root = validate_absolute_environment_root(caller_environment_root)
    expected_kind, expected_instance_id = _manifest_identity_from_location(
        manifest_path=manifest_path,
        environment_root=resolved_environment_root,
    )
    _require_manifest_paths_are_absolute(manifest)
    repository_roots = _validate_manifest_repository_roots(manifest.repository_roots)
    credentials_env_file = _resolve_manifest_optional_path(manifest.credentials_env_file)
    expected_fixture_version = None if expected_kind == "live" else FAKE_FIXTURE_VERSION
    expected_instance = resolve_environment_instance(
        kind=expected_kind,
        instance_id=expected_instance_id,
        environment_root=resolved_environment_root,
        port=_fixed_port_or_none(manifest),
        credentials_env_file=credentials_env_file,
        allowed_repository_roots=repository_roots,
        requested_repository_roots=repository_roots,
        fixture_version=expected_fixture_version,
        prepared=manifest.prepared_at is not None,
    )
    expected_manifest = _manifest_from_instance(
        expected_instance,
        prepared_at=manifest.prepared_at,
    )
    _assert_manifest_field_matches(manifest, expected_manifest, "kind")
    _assert_manifest_field_matches(manifest, expected_manifest, "instance_id")
    _assert_manifest_field_matches(manifest, expected_manifest, "environment_root")
    _assert_manifest_field_matches(manifest, expected_manifest, "instance_root")
    _assert_manifest_field_matches(manifest, expected_manifest, "db_path")
    _assert_manifest_field_matches(manifest, expected_manifest, "managed_files_root")
    _assert_manifest_field_matches(manifest, expected_manifest, "hermes_home")
    _assert_manifest_field_matches(manifest, expected_manifest, "runtime_user_home")
    _assert_manifest_field_matches(manifest, expected_manifest, "logs_dir")
    _assert_manifest_field_matches(manifest, expected_manifest, "dispatcher_lock_path")
    _assert_manifest_field_matches(manifest, expected_manifest, "server_control_socket_path")
    _assert_manifest_field_matches(manifest, expected_manifest, "port_policy")
    _assert_manifest_field_matches(manifest, expected_manifest, "credentials_env_file")
    _assert_manifest_field_matches(manifest, expected_manifest, "expected_linux_account")
    _assert_manifest_field_matches(manifest, expected_manifest, "fixture_version")
    _assert_manifest_field_matches(manifest, expected_manifest, "repository_roots")


def _manifest_identity_from_location(
    *,
    manifest_path: Path,
    environment_root: Path,
) -> tuple[EnvironmentKind, str]:
    try:
        relative_parts = manifest_path.resolve().relative_to(environment_root).parts
    except ValueError as exc:
        raise EnvironmentValidationError(
            f"invalid environment manifest location: {manifest_path}"
        ) from exc
    if relative_parts == ("live", MANIFEST_FILENAME):
        return "live", "live"
    if relative_parts == ("staging", MANIFEST_FILENAME):
        return "staging", "staging"
    raise EnvironmentValidationError(f"invalid environment manifest location: {manifest_path}")


def _require_manifest_paths_are_absolute(manifest: EnvironmentManifest) -> None:
    manifest_paths = {
        "environment_root": manifest.environment_root,
        "instance_root": manifest.instance_root,
        "db_path": manifest.db_path,
        "managed_files_root": manifest.managed_files_root,
        "hermes_home": manifest.hermes_home,
        "runtime_user_home": manifest.runtime_user_home,
        "logs_dir": manifest.logs_dir,
        "dispatcher_lock_path": manifest.dispatcher_lock_path,
        "server_control_socket_path": manifest.server_control_socket_path,
    }
    if manifest.credentials_env_file is not None:
        manifest_paths["credentials_env_file"] = manifest.credentials_env_file
    for label, path in manifest_paths.items():
        if not path.is_absolute():
            raise EnvironmentValidationError(
                f"invalid environment manifest: {label} must be absolute"
            )
    for index, repository_root in enumerate(manifest.repository_roots):
        if not repository_root.is_absolute():
            raise EnvironmentValidationError(
                f"invalid environment manifest: repository_roots[{index}] must be absolute"
            )


def _validate_manifest_repository_roots(repository_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    try:
        resolved_repository_roots = validate_repository_roots(repository_roots, repository_roots)
    except EnvironmentValidationError as exc:
        raise EnvironmentValidationError(f"invalid environment manifest: {exc}") from exc
    if resolved_repository_roots != repository_roots:
        raise EnvironmentValidationError(
            "invalid environment manifest: repository roots must be canonical"
        )
    return resolved_repository_roots


def _resolve_manifest_optional_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    resolved_path = path.resolve()
    if resolved_path != path:
        raise EnvironmentValidationError(
            "invalid environment manifest: credentials_env_file must be canonical"
        )
    return resolved_path


def _assert_manifest_field_matches(
    actual: EnvironmentManifest,
    expected: EnvironmentManifest,
    field_name: str,
) -> None:
    actual_value = getattr(actual, field_name)
    expected_value = getattr(expected, field_name)
    if actual_value != expected_value:
        raise EnvironmentValidationError(
            f"invalid environment manifest: {field_name} does not match canonical value"
        )


def _read_registry_manifests(environment_root: Path) -> tuple[EnvironmentManifest, ...]:
    resolved_environment_root = validate_absolute_environment_root(environment_root)
    manifest_paths = [
        resolved_environment_root / "live" / MANIFEST_FILENAME,
        resolved_environment_root / "staging" / MANIFEST_FILENAME,
    ]
    return tuple(
        _read_manifest(path, caller_environment_root=resolved_environment_root)
        for path in manifest_paths
        if path.exists()
    )


def _validate_registry_manifests(manifests: tuple[EnvironmentManifest, ...]) -> None:
    validate_resolved_registry(tuple(_instance_from_manifest(manifest) for manifest in manifests))


def _find_manifest(
    manifests: tuple[EnvironmentManifest, ...],
    *,
    kind: EnvironmentKind,
    instance_id: str,
) -> EnvironmentManifest | None:
    for manifest in manifests:
        if manifest.kind == kind and manifest.instance_id == instance_id:
            return manifest
    return None


def _raise_if_reprepare_mismatches_existing_manifest(
    existing: EnvironmentManifest,
    *,
    port: int | None,
    credentials_env_file: Path | None,
    repository_roots: tuple[Path, ...],
) -> None:
    if port is not None and port != _fixed_port_or_none(existing):
        raise EnvironmentValidationError("prepared environment port does not match request")
    requested_credentials_env_file = (
        credentials_env_file.resolve() if credentials_env_file is not None else None
    )
    if requested_credentials_env_file != existing.credentials_env_file:
        raise EnvironmentValidationError(
            "prepared environment credential file does not match request"
        )
    requested_repository_roots = validate_repository_roots(
        repository_roots,
        existing.repository_roots,
    )
    if requested_repository_roots != existing.repository_roots:
        raise EnvironmentValidationError("prepared environment repositories do not match request")


def _default_prepare_now(kind: EnvironmentKind, now: int | None) -> int:
    if now is not None:
        return now
    if kind == "live":
        return int(time.time())
    return 1_800_000_000


def _instance_from_manifest(manifest: EnvironmentManifest) -> ResolvedEnvironmentInstance:
    return ResolvedEnvironmentInstance(
        kind=manifest.kind,
        instance_id=manifest.instance_id,
        environment_root=manifest.environment_root,
        instance_root=manifest.instance_root,
        db_path=manifest.db_path,
        managed_files_root=manifest.managed_files_root,
        hermes_home=manifest.hermes_home,
        runtime_user_home=manifest.runtime_user_home,
        logs_dir=manifest.logs_dir,
        dispatcher_lock_path=manifest.dispatcher_lock_path,
        server_control_socket_path=manifest.server_control_socket_path,
        port_policy=manifest.port_policy,
        credentials_env_file=manifest.credentials_env_file,
        allowed_repository_roots=manifest.repository_roots,
        expected_linux_account=manifest.expected_linux_account,
        fixture_version=manifest.fixture_version,
        prepared=manifest.prepared_at is not None,
        running=False,
    )


@contextmanager
def _stopped_environment_lifecycle_lease(manifest: EnvironmentManifest) -> Iterator[None]:
    lease = PortScopedServerLifecycleLease(
        _environment_lifecycle_lease_path(manifest),
        _fixed_port_or_none(manifest) or 0,
    )
    try:
        lease.acquire()
    except ServerLifecycleAlreadyOwnedError as exc:
        raise EnvironmentValidationError("environment instance is running") from exc
    except ServerLifecycleError as exc:
        raise EnvironmentValidationError(
            f"could not prove environment instance is stopped: {exc}"
        ) from exc
    try:
        yield
    finally:
        lease.release()


def _environment_lifecycle_lease_is_held(manifest: EnvironmentManifest) -> bool:
    lease = PortScopedServerLifecycleLease(
        _environment_lifecycle_lease_path(manifest),
        _fixed_port_or_none(manifest) or 0,
    )
    try:
        lease.acquire()
    except ServerLifecycleAlreadyOwnedError:
        return True
    except ServerLifecycleError as exc:
        raise EnvironmentValidationError(
            f"could not probe environment running state: {exc}"
        ) from exc
    else:
        lease.release()
        return False


def _fixed_port_or_none(manifest: EnvironmentManifest) -> int | None:
    if isinstance(manifest.port_policy, FixedEnvironmentPort):
        return manifest.port_policy.port
    return None


def _environment_lifecycle_lease_path(manifest: EnvironmentManifest) -> Path:
    fixed_port = _fixed_port_or_none(manifest)
    if fixed_port is not None:
        return resolve_server_lifecycle_lease_path(fixed_port)
    return manifest.instance_root / "run" / "server-lifecycle.lock"


def _port_policy_from_payload(
    payload: dict[str, Any],
) -> FixedEnvironmentPort | DynamicEnvironmentPort:
    runtime_port = payload.get("runtime_port")
    if isinstance(runtime_port, dict):
        if runtime_port.get("kind") == "fixed":
            return FixedEnvironmentPort(int(runtime_port["port"]))
        if runtime_port.get("kind") == "dynamic":
            return DynamicEnvironmentPort(int(runtime_port["bind_attempts"]))
        raise EnvironmentValidationError("invalid environment runtime port policy")

    # Existing stable manifests migrate by meaning: live retains its fixed port,
    # while staging drops the previously persisted runtime port.
    if payload.get("kind") == "live" and "port" in payload:
        return FixedEnvironmentPort(int(payload["port"]))
    if payload.get("kind") == "staging":
        return DynamicEnvironmentPort()
    raise EnvironmentValidationError("invalid environment runtime port policy")
