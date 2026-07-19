from __future__ import annotations

import sys
from pathlib import Path

import pytest

from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError
from planner.environments.hermes_smoke import (
    HermesSmokeInvocation,
    smoke_prepared_nonproduction_instances,
)
from planner.minds.config import hermes_src_root


def test_hermes_smoke_runs_staging_and_preview_concurrently_with_scrubbed_envs(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    staging_env = tmp_path / "staging.env"
    preview_env = tmp_path / "preview.env"
    staging_env.write_text("ANTHROPIC_API_KEY=staging-secret\n", encoding="utf-8")
    preview_env.write_text("OPENAI_API_KEY=preview-secret\n", encoding="utf-8")
    staging = _manifest(
        kind="staging",
        instance_id="staging",
        environment_root=tmp_path / "envs",
        port=8768,
        credentials_env_file=staging_env,
        repository_roots=(repository_root,),
    )
    preview = _manifest(
        kind="preview",
        instance_id="feature-123",
        environment_root=tmp_path / "envs",
        port=9012,
        credentials_env_file=preview_env,
        repository_roots=(repository_root,),
    )
    invocations: list[HermesSmokeInvocation] = []

    def runner(invocation: HermesSmokeInvocation) -> str:
        invocations.append(invocation)
        stored = "stored-staging" if invocation.label == "staging" else "stored-preview"
        return f"[smoke] live sid=live-{invocation.label} stored={stored}\n"

    report = smoke_prepared_nonproduction_instances(
        staging,
        preview,
        hermes_python=tmp_path / "hermes-python",
        ambient_env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": "/tmp/poison",
            "HERMES_HOME": "/tmp/copied-home",
            "HERMES_SESSION_KEY": "copied-session",
            "ANTHROPIC_API_KEY": "ambient-secret",
            "PLAN_DB_PATH": "/tmp/live.db",
        },
        runner=runner,
    )

    assert report.staging_stored_session_id == "stored-staging"
    assert report.preview_stored_session_id == "stored-preview"
    assert report.staging_home == staging.hermes_home
    assert report.preview_home == preview.hermes_home
    assert {invocation.label for invocation in invocations} == {"staging", "preview"}
    for invocation in invocations:
        assert invocation.argv == [
            sys.executable,
            "-m",
            "planner.minds.smoke",
            "--home",
            str(invocation.home),
            "--hermes-python",
            str(tmp_path / "hermes-python"),
            "--prompt",
            "Reply with exactly: ok",
            "--resume",
        ]
        assert invocation.env["PATH"] == "/usr/bin:/bin"
        assert invocation.env["HERMES_HOME"] == str(invocation.home)
        assert invocation.env["HERMES_PYTHON_SRC_ROOT"] == str(
            hermes_src_root(tmp_path / "hermes-python")
        )
        assert "PYTHONPATH" not in invocation.env
        assert "HERMES_SESSION_KEY" not in invocation.env
        assert "PLAN_DB_PATH" not in invocation.env
    staging_invocation = next(
        invocation for invocation in invocations if invocation.label == "staging"
    )
    preview_invocation = next(
        invocation for invocation in invocations if invocation.label == "preview"
    )
    assert staging_invocation.env["ANTHROPIC_API_KEY"] == "staging-secret"
    assert "OPENAI_API_KEY" not in staging_invocation.env
    assert preview_invocation.env["OPENAI_API_KEY"] == "preview-secret"
    assert "ANTHROPIC_API_KEY" not in preview_invocation.env


def test_hermes_smoke_rejects_live_instances_and_equal_session_ids(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    live = _manifest(
        kind="live",
        instance_id="live",
        environment_root=tmp_path / "envs",
        port=8767,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )
    preview = _manifest(
        kind="preview",
        instance_id="feature-123",
        environment_root=tmp_path / "envs",
        port=9012,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )

    with pytest.raises(EnvironmentValidationError, match="non-production"):
        smoke_prepared_nonproduction_instances(
            live,
            preview,
            hermes_python=tmp_path / "hermes-python",
            ambient_env={},
            runner=lambda _: "[smoke] live sid=live stored=stored\n",
        )

    staging = _manifest(
        kind="staging",
        instance_id="staging",
        environment_root=tmp_path / "envs",
        port=8768,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )
    with pytest.raises(EnvironmentValidationError, match="distinct stored session ids"):
        smoke_prepared_nonproduction_instances(
            staging,
            preview,
            hermes_python=tmp_path / "hermes-python",
            ambient_env={},
            runner=lambda _: "[smoke] live sid=live stored=same-stored\n",
        )


def test_hermes_smoke_fails_clearly_when_output_has_no_stored_session_id(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    staging = _manifest(
        kind="staging",
        instance_id="staging",
        environment_root=tmp_path / "envs",
        port=8768,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )
    preview = _manifest(
        kind="preview",
        instance_id="feature-123",
        environment_root=tmp_path / "envs",
        port=9012,
        credentials_env_file=None,
        repository_roots=(repository_root,),
    )

    with pytest.raises(EnvironmentValidationError, match="printed no stored session id"):
        smoke_prepared_nonproduction_instances(
            staging,
            preview,
            hermes_python=tmp_path / "hermes-python",
            ambient_env={},
            runner=lambda _: "[smoke] ready\n",
        )


def _manifest(
    *,
    kind: str,
    instance_id: str,
    environment_root: Path,
    port: int,
    credentials_env_file: Path | None,
    repository_roots: tuple[Path, ...],
) -> EnvironmentManifest:
    environment_root = environment_root.resolve()
    instance_root = (
        environment_root / "previews" / instance_id
        if kind == "preview"
        else environment_root / instance_id
    )
    return EnvironmentManifest(
        kind=kind,  # type: ignore[arg-type]
        instance_id=instance_id,
        environment_root=environment_root,
        instance_root=instance_root,
        db_path=instance_root / "data" / "planner.db",
        managed_files_root=instance_root / "data" / "files",
        hermes_home=instance_root / "hermes-home",
        logs_dir=instance_root / "logs",
        dispatcher_lock_path=instance_root / "run" / "dispatcher.lock",
        server_control_socket_path=instance_root / "run" / "server-control.sock",
        port=port,
        credentials_env_file=credentials_env_file.resolve()
        if credentials_env_file is not None
        else None,
        expected_linux_account="panels-live" if kind == "live" else "panels-worker",
        fixture_version=None if kind == "live" else "fake-fixture-v1",
        prepared_at=1_800_000_000,
        repository_roots=tuple(root.resolve() for root in repository_roots),
    )


def _repository_root(tmp_path: Path) -> Path:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    return repository_root
