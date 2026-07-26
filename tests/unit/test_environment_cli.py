from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from planner.environments import materialize
from planner.environments.cli import EnvironmentCliDependencies, environment
from planner.environments.contracts import EnvironmentManifest, ResolvedEnvironmentInstance
from planner.environments.logic.registry import resolve_environment_instance


class _Listener:
    closed = False

    def fileno(self) -> int:
        return 42

    def close(self) -> None:
        self.closed = True


def test_help_exposes_only_live_and_staging_kinds() -> None:
    runner = CliRunner()
    for command in ("prepare", "inspect", "run", "render-linux"):
        result = runner.invoke(environment, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "live|staging" in result.output
        assert "preview" not in result.output
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
    assert "PLAN_HERMES_HOME" not in run_env


def test_live_run_uses_manifest_fixed_port_without_dynamic_listener(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    instance = resolve_environment_instance(
        kind="live",
        environment_root=_environment_root(tmp_path),
        port=45678,
        allowed_repository_roots=(repository,),
        requested_repository_roots=(repository,),
        prepared=True,
    )
    exec_calls: list[dict[str, str]] = []
    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "live",
            "--environment-root",
            str(instance.environment_root),
            "--repository-root",
            str(repository),
        ],
        obj=EnvironmentCliDependencies(
            inspect_instance=lambda **_: _manifest(instance),
            exec_fn=lambda _file, _argv, env: exec_calls.append(dict(env)),
            resolve_repository_runtime_python=lambda root: root / ".venv/bin/python",
            ambient_env={"PATH": "/usr/bin"},
        ),
    )
    assert result.exit_code == 0, result.output
    assert exec_calls[0]["PLAN_PORT"] == "45678"
    assert "PLAN_SERVER_LISTENER_FD" not in exec_calls[0]


def test_live_run_from_new_manager_execs_the_accepted_target_checkout(
    tmp_path: Path,
) -> None:
    manager_checkout = _repository(tmp_path, "manager")
    live_checkout = _repository(tmp_path, "accepted-live")
    instance = resolve_environment_instance(
        kind="live",
        environment_root=_environment_root(tmp_path),
        allowed_repository_roots=(live_checkout,),
        requested_repository_roots=(live_checkout,),
        prepared=True,
    )
    resolved_roots: list[Path] = []
    exec_calls: list[tuple[str, list[str]]] = []

    def _resolve_and_record(root: Path) -> Path:
        resolved_roots.append(root)
        return root / ".venv/bin/python"

    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "live",
            "--environment-root",
            str(instance.environment_root),
            "--repository-root",
            str(live_checkout),
        ],
        obj=EnvironmentCliDependencies(
            inspect_instance=lambda **_: _manifest(instance),
            resolve_repository_runtime_python=_resolve_and_record,
            exec_fn=lambda file, argv, _env: exec_calls.append((file, argv)),
            ambient_env={"PATH": str(manager_checkout / ".venv/bin")},
        ),
    )

    assert result.exit_code == 0, result.output
    assert resolved_roots == [live_checkout]
    target_python = str(live_checkout / ".venv/bin/python")
    assert exec_calls == [(target_python, [target_python, "-m", "planner", "serve"])]


def test_staging_run_never_opens_the_private_live_manifest(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    environment_root = _environment_root(tmp_path)
    live_checkout = _repository(tmp_path, "private-live")
    staging_checkout = _repository(tmp_path, "private-staging")
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=environment_root,
        repository_roots=(live_checkout,),
    )
    materialize.prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        repository_roots=(staging_checkout,),
    )
    live_manifest_path = environment_root / "live" / "manifest.json"
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
            str(environment_root),
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


def test_render_linux_uses_the_explicit_pinned_manager_checkout(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    manager = _repository(tmp_path, "environment-manager")
    instance = resolve_environment_instance(
        kind="live",
        environment_root=_environment_root(tmp_path),
        allowed_repository_roots=(repository,),
        requested_repository_roots=(repository,),
        prepared=True,
    )
    result = CliRunner().invoke(
        environment,
        [
            "render-linux",
            "--kind",
            "live",
            "--environment-root",
            str(instance.environment_root),
            "--environment-manager-root",
            str(manager),
            "--json",
        ],
        obj=EnvironmentCliDependencies(inspect_instance=lambda **_: _manifest(instance)),
    )

    assert result.exit_code == 0, result.output
    unit_text = json.loads(result.output)["unit_text"]
    assert "ExecStart=/opt/panels/current/bin/panels-launcher serve" in unit_text


def test_import_live_cli_has_one_explicit_state_source_contract(tmp_path: Path) -> None:
    source_db = tmp_path / "source" / "data" / "planner.db"
    source_db.parent.mkdir(parents=True)
    source_db.touch()
    source_files = tmp_path / "source" / "files"
    source_files.mkdir()
    source_hermes = tmp_path / "source" / "hermes"
    source_hermes.mkdir()
    source_user_home = tmp_path / "source" / "user-home"
    source_user_home.mkdir()
    source_logs = tmp_path / "source" / "logs"
    source_logs.mkdir()
    calls: list[dict[str, Any]] = []
    repository = _repository(tmp_path)
    instance = resolve_environment_instance(
        kind="live",
        environment_root=_environment_root(tmp_path),
        allowed_repository_roots=(repository,),
        requested_repository_roots=(repository,),
        prepared=True,
    )

    def import_live_state(**kwargs: Any) -> EnvironmentManifest:
        calls.append(kwargs)
        return _manifest(instance)

    result = CliRunner().invoke(
        environment,
        [
            "import-live",
            "--environment-root",
            str(instance.environment_root),
            "--source-db",
            str(source_db),
            "--source-managed-files-root",
            str(source_files),
            "--source-hermes-home",
            str(source_hermes),
            "--source-runtime-user-home",
            str(source_user_home),
            "--source-logs-root",
            str(source_logs),
            "--json",
        ],
        obj=EnvironmentCliDependencies(import_live_state=import_live_state),
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "environment_root": instance.environment_root,
            "source_db_path": source_db,
            "source_managed_files_root": source_files,
            "source_hermes_home": source_hermes,
            "source_runtime_user_home": source_user_home,
            "source_logs_root": source_logs,
        }
    ]


def _manifest(instance: ResolvedEnvironmentInstance) -> EnvironmentManifest:
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
