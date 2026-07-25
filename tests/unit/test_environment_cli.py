from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from planner.environments import materialize
from planner.environments.app import AppManifest
from planner.environments.cli import EnvironmentCliDependencies, environment
from planner.environments.contracts import EnvironmentManifest, ResolvedEnvironmentInstance
from planner.environments.deployment import DeploymentResult
from planner.environments.logic.registry import resolve_environment_instance


class _Listener:
    closed = False

    def fileno(self) -> int:
        return 42

    def close(self) -> None:
        self.closed = True


def test_environment_help_exposes_single_app_commands_only() -> None:
    result = CliRunner().invoke(environment, ["--help"])
    assert result.exit_code == 0, result.output
    assert "app-build" in result.output
    assert "app-identity" in result.output
    assert "app-deploy" in result.output
    assert "release-build" not in result.output
    assert "release-identity" not in result.output

    deploy_help = CliRunner().invoke(environment, ["app-deploy", "--help"])
    assert deploy_help.exit_code == 0, deploy_help.output
    assert "--candidate-app" in deploy_help.output
    assert "--current-root" in deploy_help.output
    assert "--records" not in deploy_help.output
    assert "--baseline-release" not in deploy_help.output


def test_app_identity_prints_manifest_sha(tmp_path: Path, monkeypatch: Any) -> None:
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(
        "planner.environments.cli.validate_app_manifest",
        lambda _: AppManifest("a" * 40, "b" * 64, "c" * 64),
    )
    result = CliRunner().invoke(environment, ["app-identity", "--app", str(app)])
    assert result.exit_code == 0, result.output
    assert result.output == f"{'a' * 40}\n"


def test_app_deploy_exits_nonzero_after_automatic_rollback(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result = _invoke_app_deploy(
        tmp_path,
        monkeypatch,
        DeploymentResult("rolled_back", "b" * 40, "a" * 40, "candidate unhealthy"),
    )
    assert result.exit_code != 0
    assert '"status": "rolled_back"' in result.output
    assert "app deployment rolled_back: candidate unhealthy" in result.output


def test_app_deploy_exits_nonzero_after_failed_initial_install(
    tmp_path: Path, monkeypatch: Any
) -> None:
    result = _invoke_app_deploy(
        tmp_path,
        monkeypatch,
        DeploymentResult("initial_failed", "b" * 40, None, "initial app unhealthy"),
    )
    assert result.exit_code != 0
    assert '"status": "initial_failed"' in result.output
    assert "app deployment initial_failed: initial app unhealthy" in result.output


def test_app_deploy_success_and_unchanged_exit_zero(
    tmp_path: Path, monkeypatch: Any
) -> None:
    for status in ("succeeded", "unchanged"):
        result = _invoke_app_deploy(
            tmp_path,
            monkeypatch,
            DeploymentResult(status, "b" * 40, "a" * 40, None),
        )
        assert result.exit_code == 0, result.output
        assert f'"status": "{status}"' in result.output


def _invoke_app_deploy(
    tmp_path: Path,
    monkeypatch: Any,
    deployment_result: DeploymentResult,
):
    candidate = tmp_path / "candidate"
    candidate.mkdir(exist_ok=True)
    monkeypatch.setattr(
        "planner.environments.cli.deploy_app", lambda **_: deployment_result
    )
    return CliRunner().invoke(
        environment,
        [
            "app-deploy",
            "--candidate-app",
            str(candidate),
            "--current-root",
            str(tmp_path / "current"),
            "--source-db",
            str(tmp_path / "planning.db"),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--health-url",
            "http://127.0.0.1:8767/api/health",
            "--service-manager",
            "systemctl",
            "--service-name",
            "panels-live",
        ],
    )


def test_help_exposes_live_contract_and_staging_only_source_run() -> None:
    runner = CliRunner()
    for command in ("prepare", "inspect"):
        result = runner.invoke(environment, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "live|staging" in result.output
        assert "preview" not in result.output
    run_result = runner.invoke(environment, ["run", "--help"])
    assert run_result.exit_code == 0, run_result.output
    assert "[staging]" in run_result.output
    assert "live|staging" not in run_result.output
    result = runner.invoke(environment, ["--help"])
    assert "smoke-hermes" not in result.output


def test_staging_run_selects_runtime_port_and_passes_reserved_listener(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    repository = _repository(tmp_path)
    instance = resolve_environment_instance(
        kind="staging",
        environment_root=_environment_root(tmp_path),
        allowed_repository_roots=(repository,),
        requested_repository_roots=(repository,),
        prepared=True,
    )
    listener = _Listener()
    monkeypatch.setattr(
        "planner.environments.cli.reserve_available_tcp_listener",
        lambda **_: (listener, 43123),
    )
    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []

    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "staging",
            "--environment-root",
            str(instance.environment_root),
            "--repository-root",
            str(repository),
            "--test-mode",
        ],
        obj=EnvironmentCliDependencies(
            inspect_instance=lambda **_: _manifest(instance),
            exec_fn=lambda file, argv, env: exec_calls.append((file, argv, dict(env))),
            resolve_repository_runtime_python=lambda root: root / ".venv/bin/python",
            ambient_env={
                "PATH": "/usr/bin",
                "HOME": "/operator-home",
                "PLAN_DB_PATH": "/poison",
            },
        ),
    )

    assert result.exit_code == 0, result.output
    assert "staging http://127.0.0.1:43123" in result.output
    run_env = exec_calls[0][2]
    assert run_env["PLAN_PORT"] == "43123"
    assert run_env["PLAN_SERVER_LISTENER_FD"] == "42"
    assert run_env["PLAN_SERVER_LIFECYCLE_LEASE_PATH"].endswith(
        "/staging/run/server-lifecycle.lock"
    )
    assert run_env["PLAN_DB_PATH"] == str(instance.db_path)
    assert run_env["HOME"] == "/operator-home"
    assert exec_calls[0][0] == str(repository / ".venv/bin/python")
    assert exec_calls[0][1] == [
        str(repository / ".venv/bin/python"),
        "-m",
        "planner",
        "serve",
    ]
    assert listener.closed is True
    assert run_env["PLAN_HERMES_HOME"] == "/operator-home/.hermes"


def test_live_cannot_run_from_a_source_checkout(tmp_path: Path) -> None:
    live_checkout = _repository(tmp_path, "live-source")
    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "live",
            "--environment-root",
            str(_environment_root(tmp_path)),
            "--repository-root",
            str(live_checkout),
        ],
    )

    assert result.exit_code == 2
    assert "Invalid value for '--kind'" in result.output


def test_staging_run_never_opens_the_private_live_manifest(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    live_environment_root = _environment_root(tmp_path) / "deployments"
    staging_environment_root = _environment_root(tmp_path) / "coding"
    staging_checkout = _repository(tmp_path, "private-staging")
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=live_environment_root,
        repository_roots=(),
    )
    materialize.prepare_environment_instance(
        kind="staging",
        environment_root=staging_environment_root,
        repository_roots=(staging_checkout,),
    )
    live_manifest_path = live_environment_root / "manifest.json"
    real_read_manifest = materialize._read_manifest

    def reject_live_manifest(path: Path, **kwargs: Any) -> EnvironmentManifest:
        if path == live_manifest_path:
            raise AssertionError("staging runtime opened the private live manifest")
        return real_read_manifest(path, **kwargs)

    monkeypatch.setattr(materialize, "_read_manifest", reject_live_manifest)
    listener = _Listener()
    monkeypatch.setattr(
        "planner.environments.cli.reserve_available_tcp_listener",
        lambda **_: (listener, 43125),
    )
    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "staging",
            "--environment-root",
            str(staging_environment_root),
            "--repository-root",
            str(staging_checkout),
        ],
        obj=EnvironmentCliDependencies(
            exec_fn=lambda _file, _argv, _env: None,
            resolve_repository_runtime_python=lambda root: root / ".venv/bin/python",
            ambient_env={"PATH": "/usr/bin"},
        ),
    )

    assert result.exit_code == 0, result.output
    assert listener.closed is True


def test_prepare_staging_json_has_no_port_or_running(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    instance = resolve_environment_instance(
        kind="staging",
        environment_root=_environment_root(tmp_path),
        allowed_repository_roots=(repository,),
        requested_repository_roots=(repository,),
        fixture_version="fake-v1",
        prepared=True,
    )
    result = CliRunner().invoke(
        environment,
        [
            "prepare",
            "--kind",
            "staging",
            "--environment-root",
            str(instance.environment_root),
            "--repository-root",
            str(repository),
            "--json",
        ],
        obj=EnvironmentCliDependencies(prepare_instance=lambda **_: _manifest(instance)),
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["runtime_port_policy"] == "dynamic"
    assert "port" not in payload
    assert "running" not in payload


def _manifest(instance: ResolvedEnvironmentInstance) -> EnvironmentManifest:
    return EnvironmentManifest(
        kind=instance.kind,
        instance_id=instance.instance_id,
        environment_root=instance.environment_root,
        instance_root=instance.instance_root,
        db_path=instance.db_path,
        managed_files_root=instance.managed_files_root,
        logs_dir=instance.logs_dir,
        dispatcher_lock_path=instance.dispatcher_lock_path,
        server_control_socket_path=instance.server_control_socket_path,
        port_policy=instance.port_policy,
        credentials_env_file=instance.credentials_env_file,
        fixture_version=instance.fixture_version,
        prepared_at=123,
        repository_roots=instance.allowed_repository_roots,
    )


def _repository(tmp_path: Path, name: str = "repo") -> Path:
    repository = tmp_path / name
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "src" / "planner" / "skills",
        repository / "src" / "planner" / "skills",
    )
    return repository.resolve()


def _environment_root(tmp_path: Path) -> Path:
    return Path("/tmp") / f"pe-{tmp_path.parent.name}-{tmp_path.name}"
