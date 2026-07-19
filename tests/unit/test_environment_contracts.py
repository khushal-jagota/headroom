from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.contracts import (
    EnvironmentDefaults,
    EnvironmentPortRange,
    EnvironmentValidationError,
    ResolvedEnvironmentInstance,
)
from planner.environments.logic.registry import resolve_environment_instance
from planner.environments.logic.validation import (
    validate_absolute_environment_root,
    validate_instance_id,
    validate_repository_roots,
    validate_resolved_registry,
)


def test_default_instances_resolve_to_isolated_layouts(tmp_path: Path) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    environment_root = _short_environment_root(tmp_path)

    live = resolve_environment_instance(
        kind="live",
        environment_root=environment_root,
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
    )
    staging = resolve_environment_instance(
        kind="staging",
        environment_root=environment_root,
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
    )
    preview = resolve_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=environment_root,
        port=9012,
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
    )

    assert live == ResolvedEnvironmentInstance(
        kind="live",
        instance_id="live",
        environment_root=environment_root.resolve(),
        instance_root=(environment_root / "live").resolve(),
        db_path=(environment_root / "live" / "data" / "planner.db").resolve(),
        managed_files_root=(environment_root / "live" / "data" / "files").resolve(),
        hermes_home=(environment_root / "live" / "hermes-home").resolve(),
        logs_dir=(environment_root / "live" / "logs").resolve(),
        dispatcher_lock_path=(environment_root / "live" / "run" / "dispatcher.lock").resolve(),
        server_control_socket_path=(
            environment_root / "live" / "run" / "server-control.sock"
        ).resolve(),
        port=8767,
        credentials_env_file=None,
        allowed_repository_roots=(repository_root.resolve(),),
        expected_linux_account="panels-live",
        fixture_version=None,
        prepared=False,
        running=False,
    )
    assert staging.instance_id == "staging"
    assert staging.instance_root == (environment_root / "staging").resolve()
    assert staging.port == 8768
    assert staging.expected_linux_account == "panels-worker"
    assert preview.instance_root == (environment_root / "previews" / "feature-123").resolve()
    assert preview.expected_linux_account == "panels-worker"


@pytest.mark.parametrize(
    ("kind", "instance_id"),
    [
        ("live", "other"),
        ("staging", "other"),
        ("preview", ""),
        ("preview", "."),
        ("preview", ".."),
        ("preview", "Feature"),
        ("preview", "feature/one"),
        ("preview", "feature one"),
        ("preview", "feature_one"),
    ],
)
def test_instance_ids_are_unambiguous(kind: str, instance_id: str) -> None:
    with pytest.raises(EnvironmentValidationError):
        validate_instance_id(kind, instance_id)  # type: ignore[arg-type]


def test_environment_root_must_be_absolute() -> None:
    with pytest.raises(EnvironmentValidationError):
        validate_absolute_environment_root(Path("relative-envs"))


def test_control_socket_path_length_is_rejected_during_resolution(tmp_path: Path) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    environment_root = tmp_path / ("very-long-environment-root-name-" + ("x" * 120))

    with pytest.raises(EnvironmentValidationError, match="server control socket path is too long"):
        resolve_environment_instance(
            kind="preview",
            instance_id="feature-123",
            environment_root=environment_root,
            port=9012,
            allowed_repository_roots=(repository_root,),
            requested_repository_roots=(repository_root,),
        )


@pytest.mark.parametrize("port", [1, 65535])
def test_explicit_ports_accept_tcp_boundaries(tmp_path: Path, port: int) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / ".git").mkdir()

    preview = resolve_environment_instance(
        kind="preview",
        instance_id=f"port-{port}",
        environment_root=_short_environment_root(tmp_path),
        port=port,
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
    )

    assert preview.port == port


@pytest.mark.parametrize("port", [-1, 0, 65536])
def test_explicit_ports_must_be_valid_tcp_ports(tmp_path: Path, port: int) -> None:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / ".git").mkdir()

    with pytest.raises(EnvironmentValidationError, match="port"):
        resolve_environment_instance(
            kind="preview",
            instance_id="feature-123",
            environment_root=_short_environment_root(tmp_path),
            port=port,
            allowed_repository_roots=(repository_root,),
            requested_repository_roots=(repository_root,),
        )


def test_default_ports_accept_tcp_boundaries() -> None:
    defaults = EnvironmentDefaults(
        live_port=1,
        staging_port=65535,
        preview_ports=EnvironmentPortRange(2, 65534),
    )

    assert defaults.live_port == 1
    assert defaults.staging_port == 65535
    assert defaults.preview_ports == EnvironmentPortRange(2, 65534)


@pytest.mark.parametrize(
    "defaults",
    [
        EnvironmentDefaults(live_port=1, staging_port=2),
        EnvironmentDefaults(live_port=2, staging_port=1),
    ],
)
def test_default_live_and_staging_ports_can_be_distinct_from_preview_range(
    defaults: EnvironmentDefaults,
) -> None:
    assert defaults.live_port != defaults.staging_port


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EnvironmentDefaults(live_port=0),
        lambda: EnvironmentDefaults(live_port=65536),
        lambda: EnvironmentDefaults(staging_port=0),
        lambda: EnvironmentDefaults(staging_port=65536),
        lambda: EnvironmentDefaults(live_port=8768, staging_port=8768),
        lambda: EnvironmentDefaults(live_port=9000),
        lambda: EnvironmentDefaults(staging_port=9999),
    ],
)
def test_default_ports_reject_invalid_duplicates_and_preview_overlap(
    factory: Callable[[], EnvironmentDefaults],
) -> None:
    with pytest.raises(EnvironmentValidationError, match="port"):
        factory()


def test_preview_port_range_accepts_tcp_boundaries() -> None:
    assert EnvironmentPortRange(1, 65535).start == 1


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EnvironmentPortRange(-1, 10),
        lambda: EnvironmentPortRange(0, 10),
        lambda: EnvironmentPortRange(10, 65536),
        lambda: EnvironmentPortRange(10, 9),
    ],
)
def test_preview_port_range_must_stay_inside_tcp_boundaries(
    factory: Callable[[], EnvironmentPortRange],
) -> None:
    with pytest.raises(EnvironmentValidationError, match="port"):
        factory()


def test_repository_roots_must_resolve_to_allowed_existing_worktrees(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / ".git").mkdir()

    missing = tmp_path / "missing"
    disallowed = tmp_path / "disallowed"
    disallowed.mkdir()
    (disallowed / ".git").mkdir()

    assert validate_repository_roots((allowed,), (allowed,)) == (allowed.resolve(),)
    assert validate_repository_roots((allowed / ".." / "allowed",), (allowed,)) == (
        allowed.resolve(),
    )

    with pytest.raises(EnvironmentValidationError):
        validate_repository_roots((missing,), (allowed,))
    with pytest.raises(EnvironmentValidationError):
        validate_repository_roots((disallowed,), (allowed,))


def test_registry_rejects_duplicate_ports_and_cross_instance_path_overlap(
    tmp_path: Path,
) -> None:
    live, staging = _live_and_staging(tmp_path)

    with pytest.raises(EnvironmentValidationError):
        validate_resolved_registry((live, replace(staging, port=live.port)))

    with pytest.raises(EnvironmentValidationError):
        validate_resolved_registry((live, replace(staging, hermes_home=live.hermes_home)))

    with pytest.raises(EnvironmentValidationError):
        validate_resolved_registry(
            (
                live,
                replace(staging, instance_root=live.instance_root / "nested-staging"),
            )
        )

    with pytest.raises(EnvironmentValidationError):
        validate_resolved_registry(
            (
                live,
                replace(staging, server_control_socket_path=live.instance_root / "run" / "sock"),
            )
        )


def test_registry_rejects_cross_instance_repository_root_overlap(
    tmp_path: Path,
) -> None:
    live, staging = _live_and_staging(tmp_path)
    shared_repository_root = _repository_root(tmp_path, "shared-repo")
    live_with_shared_repository = replace(
        live,
        allowed_repository_roots=(shared_repository_root,),
    )
    staging_with_shared_repository = replace(
        staging,
        allowed_repository_roots=(shared_repository_root,),
    )

    with pytest.raises(EnvironmentValidationError, match="repository_roots"):
        validate_resolved_registry(
            (live_with_shared_repository, staging_with_shared_repository)
        )


def test_registry_rejects_nested_repository_roots_across_instances(
    tmp_path: Path,
) -> None:
    live, staging = _live_and_staging(tmp_path)
    live_repository_root = _repository_root(tmp_path, "outer-live-repo")
    nested_staging_repository_root = live_repository_root / "nested-staging"
    nested_staging_repository_root.mkdir()
    (nested_staging_repository_root / ".git").mkdir()

    with pytest.raises(EnvironmentValidationError, match="repository_roots"):
        validate_resolved_registry(
            (
                replace(live, allowed_repository_roots=(live_repository_root,)),
                replace(staging, allowed_repository_roots=(nested_staging_repository_root,)),
            )
        )


def test_registry_rejects_nonproduction_paths_and_env_files_under_live(
    tmp_path: Path,
) -> None:
    live, staging = _live_and_staging(tmp_path)

    with pytest.raises(EnvironmentValidationError):
        validate_resolved_registry(
            (
                live,
                replace(staging, db_path=live.instance_root / "staging.db"),
            )
        )

    with pytest.raises(EnvironmentValidationError):
        validate_resolved_registry(
            (
                live,
                replace(staging, credentials_env_file=live.instance_root / "staging.env"),
            )
        )

    with pytest.raises(EnvironmentValidationError):
        validate_resolved_registry(
            (
                live,
                replace(staging, credentials_env_file=live.credentials_env_file),
            )
        )


def _live_and_staging(
    tmp_path: Path,
) -> tuple[ResolvedEnvironmentInstance, ResolvedEnvironmentInstance]:
    live_repository_root = _repository_root(tmp_path, "live-repo")
    staging_repository_root = _repository_root(tmp_path, "staging-repo")
    environment_root = _short_environment_root(tmp_path)

    live = resolve_environment_instance(
        kind="live",
        environment_root=environment_root,
        credentials_env_file=environment_root / "live" / "live.env",
        allowed_repository_roots=(live_repository_root,),
        requested_repository_roots=(live_repository_root,),
    )
    staging = resolve_environment_instance(
        kind="staging",
        environment_root=environment_root,
        credentials_env_file=environment_root / "staging" / "staging.env",
        allowed_repository_roots=(staging_repository_root,),
        requested_repository_roots=(staging_repository_root,),
    )
    return live, staging


def _repository_root(tmp_path: Path, name: str) -> Path:
    repository_root = tmp_path / name
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    return repository_root


def _short_environment_root(tmp_path: Path) -> Path:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    return Path("/tmp") / f"pe-contracts-{os.getpid()}-{digest}"
