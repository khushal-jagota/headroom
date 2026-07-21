"""Filesystem materialization for isolated Panels runtime environments."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from planner.environments.contracts import (
    EnvironmentDefaults,
    EnvironmentKind,
    EnvironmentManifest,
    EnvironmentValidationError,
    ResolvedEnvironmentInstance,
    validate_tcp_port,
)
from planner.environments.fake_fixture import (
    FAKE_FIXTURE_VERSION,
    build_fake_environment_database,
)
from planner.environments.logic.credentials import parse_environment_file
from planner.environments.logic.registry import (
    acquire_environment_registry_lock,
    allocate_preview_port,
    resolve_environment_instance,
)
from planner.environments.logic.validation import (
    validate_absolute_environment_root,
    validate_instance_id,
    validate_nonproduction_credential_reference_outside_live_root,
    validate_repository_roots,
    validate_resolved_registry,
)
from planner.minds.config import provision_planner_home_skills
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

        allocated_port = port
        if kind == "preview" and allocated_port is None:
            allocated_port = allocate_preview_port(
                used_ports={manifest.port for manifest in registry_manifests},
                defaults=resolved_defaults,
            )

        fixture_version = None if kind == "live" else FAKE_FIXTURE_VERSION
        instance = resolve_environment_instance(
            kind=kind,
            instance_id=resolved_instance_id,
            environment_root=resolved_environment_root,
            port=allocated_port,
            credentials_env_file=credentials_env_file,
            allowed_repository_roots=repository_roots,
            requested_repository_roots=repository_roots,
            defaults=resolved_defaults,
            fixture_version=fixture_version,
            prepared=True,
        )
        manifest = _manifest_from_instance(instance, prepared_at=effective_now)
        _validate_registry_manifests((*registry_manifests, manifest))
        _prepare_common_layout(instance)
        if kind != "live":
            _replace_data_tree_from_fixture(instance.db_path, now=effective_now)
        else:
            instance.managed_files_root.mkdir(parents=True, exist_ok=True)
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
            port=current.port,
            credentials_env_file=current.credentials_env_file,
            allowed_repository_roots=current.repository_roots,
            requested_repository_roots=current.repository_roots,
            fixture_version=FAKE_FIXTURE_VERSION,
            prepared=True,
        )
        _validate_registry_manifests(_read_registry_manifests(current.environment_root))
        with _stopped_port_lifecycle_lease(current):
            _prepare_common_layout(instance)
            _replace_data_tree_from_fixture(instance.db_path, now=now)
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
        _validate_registry_manifests(_read_registry_manifests(current.environment_root))
        with _stopped_port_lifecycle_lease(current):
            shutil.rmtree(current.instance_root)
        return current


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
        _validate_registry_manifests(_read_registry_manifests(resolved_environment_root))
        if repository_roots:
            validate_repository_roots(repository_roots, manifest.repository_roots)
        if port is not None and port != manifest.port:
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
    running_value = _port_lifecycle_lease_is_held(manifest.port) if running is None else running
    return {
        "kind": manifest.kind,
        "instance_id": manifest.instance_id,
        "environment_root": str(manifest.environment_root),
        "instance_root": str(manifest.instance_root),
        "db_path": str(manifest.db_path),
        "managed_files_root": str(manifest.managed_files_root),
        "hermes_home": str(manifest.hermes_home),
        "logs_dir": str(manifest.logs_dir),
        "dispatcher_lock_path": str(manifest.dispatcher_lock_path),
        "server_control_socket_path": str(manifest.server_control_socket_path),
        "port": manifest.port,
        "credentials_env_file": (
            str(manifest.credentials_env_file)
            if manifest.credentials_env_file is not None
            else None
        ),
        "repository_roots": [str(root) for root in manifest.repository_roots],
        "expected_linux_account": manifest.expected_linux_account,
        "fixture_version": manifest.fixture_version,
        "prepared": prepared_value,
        "running": running_value,
        "prepared_at": manifest.prepared_at,
    }


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
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{data_root.name}-", dir=str(instance_root))
    )
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


def _prepare_common_layout(instance) -> None:  # type: ignore[no-untyped-def]
    instance.instance_root.mkdir(parents=True, exist_ok=True)
    instance.logs_dir.mkdir(parents=True, exist_ok=True)
    instance.dispatcher_lock_path.parent.mkdir(parents=True, exist_ok=True)
    instance.server_control_socket_path.parent.mkdir(parents=True, exist_ok=True)
    provision_planner_home_skills(instance.hermes_home)


def _manifest_from_instance(instance, *, prepared_at: int | None) -> EnvironmentManifest:  # type: ignore[no-untyped-def]
    return EnvironmentManifest(
        kind=instance.kind,
        instance_id=instance.instance_id,
        environment_root=instance.environment_root,
        instance_root=instance.instance_root,
        db_path=instance.db_path,
        managed_files_root=instance.managed_files_root,
        hermes_home=instance.hermes_home,
        logs_dir=instance.logs_dir,
        dispatcher_lock_path=instance.dispatcher_lock_path,
        server_control_socket_path=instance.server_control_socket_path,
        port=instance.port,
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
    resolved_instance_id = validate_instance_id(kind, instance_id)
    if kind == "live":
        return resolved_environment_root / "live" / MANIFEST_FILENAME
    if kind == "staging":
        return resolved_environment_root / "staging" / MANIFEST_FILENAME
    if kind == "preview":
        return resolved_environment_root / "previews" / resolved_instance_id / MANIFEST_FILENAME
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
            logs_dir=Path(payload["logs_dir"]),
            dispatcher_lock_path=Path(payload["dispatcher_lock_path"]),
            server_control_socket_path=Path(payload["server_control_socket_path"]),
            port=validate_tcp_port(int(payload["port"])),
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
    payload = asdict(manifest)
    for key, value in tuple(payload.items()):
        if isinstance(value, Path):
            payload[key] = str(value)
    payload["repository_roots"] = [str(root) for root in manifest.repository_roots]
    payload["credentials_env_file"] = (
        str(manifest.credentials_env_file)
        if manifest.credentials_env_file is not None
        else None
    )
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
        port=manifest.port,
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
    _assert_manifest_field_matches(manifest, expected_manifest, "logs_dir")
    _assert_manifest_field_matches(manifest, expected_manifest, "dispatcher_lock_path")
    _assert_manifest_field_matches(manifest, expected_manifest, "server_control_socket_path")
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
    if (
        len(relative_parts) == 3
        and relative_parts[0] == "previews"
        and relative_parts[2] == MANIFEST_FILENAME
    ):
        return "preview", validate_instance_id("preview", relative_parts[1])
    raise EnvironmentValidationError(f"invalid environment manifest location: {manifest_path}")


def _require_manifest_paths_are_absolute(manifest: EnvironmentManifest) -> None:
    manifest_paths = {
        "environment_root": manifest.environment_root,
        "instance_root": manifest.instance_root,
        "db_path": manifest.db_path,
        "managed_files_root": manifest.managed_files_root,
        "hermes_home": manifest.hermes_home,
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
        raise EnvironmentValidationError(
            f"invalid environment manifest: {exc}"
        ) from exc
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
    previews_root = resolved_environment_root / "previews"
    if previews_root.exists():
        manifest_paths.extend(sorted(previews_root.glob(f"*/{MANIFEST_FILENAME}")))
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
    if port is not None and port != existing.port:
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
        logs_dir=manifest.logs_dir,
        dispatcher_lock_path=manifest.dispatcher_lock_path,
        server_control_socket_path=manifest.server_control_socket_path,
        port=manifest.port,
        credentials_env_file=manifest.credentials_env_file,
        allowed_repository_roots=manifest.repository_roots,
        expected_linux_account=manifest.expected_linux_account,
        fixture_version=manifest.fixture_version,
        prepared=manifest.prepared_at is not None,
        running=False,
    )


@contextmanager
def _stopped_port_lifecycle_lease(manifest: EnvironmentManifest) -> Iterator[None]:
    lease = PortScopedServerLifecycleLease(
        resolve_server_lifecycle_lease_path(manifest.port),
        manifest.port,
    )
    try:
        lease.acquire()
    except ServerLifecycleAlreadyOwnedError as exc:
        raise EnvironmentValidationError(
            f"environment instance is running on port {manifest.port}"
        ) from exc
    except ServerLifecycleError as exc:
        raise EnvironmentValidationError(
            f"could not prove environment instance is stopped: {exc}"
        ) from exc
    try:
        yield
    finally:
        lease.release()


def _port_lifecycle_lease_is_held(port: int) -> bool:
    lease = PortScopedServerLifecycleLease(resolve_server_lifecycle_lease_path(port), port)
    try:
        lease.acquire()
    except ServerLifecycleAlreadyOwnedError:
        return True
    except ServerLifecycleError as exc:
        raise EnvironmentValidationError(
            f"could not probe environment running state for port {port}: {exc}"
        ) from exc
    else:
        lease.release()
        return False
