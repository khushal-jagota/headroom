from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.contracts import (
    DynamicEnvironmentPort,
    EnvironmentDefaults,
    EnvironmentValidationError,
    FixedEnvironmentPort,
)
from planner.environments.logic.registry import resolve_environment_instance
from planner.environments.logic.validation import (
    validate_instance_id,
    validate_repository_roots,
    validate_resolved_registry,
)


def test_live_and_staging_have_distinct_runtime_port_contracts(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    environment_root = _environment_root(tmp_path)

    live = resolve_environment_instance(
        kind="live",
        environment_root=environment_root,
    )
    staging = resolve_environment_instance(
        kind="staging",
        environment_root=environment_root,
        allowed_repository_roots=(repository,),
        requested_repository_roots=(repository,),
    )

    assert live.port_policy == FixedEnvironmentPort(8767)
    assert staging.port_policy == DynamicEnvironmentPort(bind_attempts=10)
    assert live.instance_root == environment_root.resolve()
    assert live.db_path == live.instance_root / "current" / "data" / "planner.db"
    assert live.logs_dir == live.instance_root / "current" / "logs"
    assert live.dispatcher_lock_path == live.instance_root / "current" / "data" / "dispatcher.lock"
    assert live.server_control_socket_path == (
        live.instance_root / "current" / "data" / "server-control.sock"
    )
    assert staging.instance_root == environment_root.resolve() / "staging"
    assert staging.dispatcher_lock_path == staging.instance_root / "run" / "dispatcher.lock"


def test_staging_rejects_a_configured_port(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(EnvironmentValidationError, match="selects its port at launch"):
        resolve_environment_instance(
            kind="staging",
            environment_root=_environment_root(tmp_path),
            port=9000,
            allowed_repository_roots=(repository,),
            requested_repository_roots=(repository,),
        )


def test_live_rejects_source_repository_registration(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    with pytest.raises(EnvironmentValidationError, match="deployed app"):
        resolve_environment_instance(
            kind="live",
            environment_root=_environment_root(tmp_path),
            allowed_repository_roots=(repository,),
            requested_repository_roots=(repository,),
        )


def test_live_rejects_source_repository_and_credential_file_registration(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    environment_root = _environment_root(tmp_path)

    with pytest.raises(EnvironmentValidationError, match="source repository"):
        resolve_environment_instance(
            kind="live",
            environment_root=environment_root,
            allowed_repository_roots=(repository,),
            requested_repository_roots=(repository,),
        )

    with pytest.raises(EnvironmentValidationError, match="credential file"):
        resolve_environment_instance(
            kind="live",
            environment_root=environment_root,
            credentials_env_file=tmp_path / "live.env",
        )


def test_preview_is_not_an_environment_kind(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentValidationError, match="unknown environment kind"):
        validate_instance_id("preview", "feature")  # type: ignore[arg-type]


def test_fake_dot_git_directory_is_not_a_worktree(tmp_path: Path) -> None:
    fake = tmp_path / "fake"
    (fake / ".git").mkdir(parents=True)
    with pytest.raises(EnvironmentValidationError, match="not a worktree"):
        validate_repository_roots((fake,), (fake,))


def test_runtime_port_policies_validate_bounds() -> None:
    with pytest.raises(EnvironmentValidationError, match="fixed environment port"):
        FixedEnvironmentPort(0)
    with pytest.raises(EnvironmentValidationError, match="bind attempts"):
        DynamicEnvironmentPort(0)
    with pytest.raises(EnvironmentValidationError, match="live default port"):
        EnvironmentDefaults(live_port=65536)


def test_registry_only_compares_fixed_ports(tmp_path: Path) -> None:
    live, staging = _instances(tmp_path)
    validate_resolved_registry((live, staging))
    with pytest.raises(EnvironmentValidationError, match="duplicate port"):
        validate_resolved_registry((live, replace(staging, port_policy=FixedEnvironmentPort(8767))))


def test_registry_rejects_cross_environment_state_overlap(tmp_path: Path) -> None:
    live, staging = _instances(tmp_path)
    with pytest.raises(EnvironmentValidationError, match="overlapping paths"):
        validate_resolved_registry((live, replace(staging, logs_dir=live.logs_dir)))


def _instances(tmp_path: Path):  # type: ignore[no-untyped-def]
    staging_repository = _repository(tmp_path, "staging-repo")
    live_root = _environment_root(tmp_path) / "live-estate"
    staging_root = _environment_root(tmp_path) / "source-estate"
    live = resolve_environment_instance(
        kind="live",
        environment_root=live_root,
    )
    staging = resolve_environment_instance(
        kind="staging",
        environment_root=staging_root,
        allowed_repository_roots=(staging_repository,),
        requested_repository_roots=(staging_repository,),
    )
    return live, staging


def _repository(tmp_path: Path, name: str = "repo") -> Path:
    repository = tmp_path / name
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    return repository.resolve()


def _environment_root(tmp_path: Path) -> Path:
    return Path("/tmp") / f"pe-{tmp_path.parent.name}-{tmp_path.name}"
