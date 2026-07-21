from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from planner.environments import materialize
from planner.environments.cli import EnvironmentCliDependencies, environment
from planner.environments.contracts import EnvironmentManifest
from planner.environments.logic.registry import resolve_environment_instance
from planner.environments.materialize import prepare_environment_instance
from planner.server_lifecycle.contracts import ServerLifecycleError


def test_run_requires_prepared_instance(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    unprepared = _manifest(
        resolve_environment_instance(
            kind="staging",
            environment_root=_short_environment_root(tmp_path),
            allowed_repository_roots=(repository_root,),
            requested_repository_roots=(repository_root,),
            prepared=False,
        ),
        prepared_at=None,
    )
    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []

    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "staging",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
        ],
        obj=EnvironmentCliDependencies(
            inspect_instance=lambda **_: unprepared,
            exec_fn=lambda file, argv, env: exec_calls.append((file, argv, dict(env))),
            ambient_env={"PATH": "/usr/bin:/bin"},
        ),
    )

    assert result.exit_code != 0
    assert "environment instance is not prepared" in result.output
    assert exec_calls == []


def test_run_execs_planner_serve_with_scrubbed_environment(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    env_file = tmp_path / "credentials.env"
    env_file.write_text("ANTHROPIC_API_KEY=file-secret", encoding="utf-8")
    prepared = resolve_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        credentials_env_file=env_file,
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
        prepared=True,
    )
    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []
    inspected_calls: list[dict[str, Any]] = []
    cwd_observed_by_exec: list[Path] = []
    cwd_before = Path.cwd()

    def inspect_instance(**kwargs: Any) -> EnvironmentManifest:
        inspected_calls.append(kwargs)
        return _manifest(prepared)

    def exec_fn(file: str, argv: list[str], env: dict[str, str]) -> None:
        cwd_observed_by_exec.append(Path.cwd())
        exec_calls.append((file, argv, dict(env)))

    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "staging",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
        ],
        obj=EnvironmentCliDependencies(
            resolve_instance=lambda **_: (_ for _ in ()).throw(AssertionError("not used")),
            inspect_instance=inspect_instance,
            exec_fn=exec_fn,
            ambient_env={
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": "/tmp/poison",
                "PLAN_ACTOR": "chief",
                "PLAN_TICKET_ID": "t_poison",
                "PLAN_DB_PATH": "/tmp/poison.db",
                "HERMES_SESSION_KEY": "sess_poison",
                "ANTHROPIC_API_KEY": "ambient-secret",
            },
        ),
    )

    assert result.exit_code == 0, result.output
    assert inspected_calls == [
        {
            "kind": "staging",
            "instance_id": None,
            "environment_root": _short_environment_root(tmp_path),
            "port": None,
            "credentials_env_file": None,
            "repository_roots": (repository_root,),
        }
    ]
    assert len(exec_calls) == 1
    assert cwd_observed_by_exec == [repository_root.resolve()]
    assert Path.cwd() == cwd_before
    executable, argv, run_env = exec_calls[0]
    assert executable == sys.executable
    assert argv == [sys.executable, "-m", "planner", "serve"]
    assert run_env["PATH"] == "/usr/bin:/bin"
    assert run_env["ANTHROPIC_API_KEY"] == "file-secret"
    assert run_env["PLAN_DB_PATH"] == str(prepared.db_path)
    assert run_env["PLAN_PORT"] == str(prepared.port)
    assert run_env["PLAN_HERMES_HOME"] == str(prepared.hermes_home)
    assert "PYTHONPATH" not in run_env
    assert "PLAN_ACTOR" not in run_env
    assert "PLAN_TICKET_ID" not in run_env
    assert "HERMES_SESSION_KEY" not in run_env


def test_run_requires_exactly_one_caller_trusted_repository_root(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    second_repository_root = tmp_path / "second-repo"
    second_repository_root.mkdir()
    (second_repository_root / ".git").mkdir()
    prepared = resolve_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        allowed_repository_roots=(repository_root, second_repository_root),
        requested_repository_roots=(repository_root, second_repository_root),
        prepared=True,
    )
    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []

    missing = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "staging",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
        ],
        obj=EnvironmentCliDependencies(
            inspect_instance=lambda **_: _manifest(prepared),
            exec_fn=lambda file, argv, env: exec_calls.append((file, argv, dict(env))),
            ambient_env={"PATH": "/usr/bin:/bin"},
        ),
    )
    repeated = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "staging",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
            "--repository-root",
            str(second_repository_root),
        ],
        obj=EnvironmentCliDependencies(
            inspect_instance=lambda **_: _manifest(prepared),
            exec_fn=lambda file, argv, env: exec_calls.append((file, argv, dict(env))),
            ambient_env={"PATH": "/usr/bin:/bin"},
        ),
    )

    assert missing.exit_code != 0
    assert "exactly one repository root" in missing.output
    assert repeated.exit_code != 0
    assert "exactly one repository root" in repeated.output
    assert exec_calls == []


def test_run_hidden_test_mode_injects_fake_runtime_from_the_seam(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    prepared = resolve_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9012,
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
        prepared=True,
    )
    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []

    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
            "--test-mode",
        ],
        obj=EnvironmentCliDependencies(
            inspect_instance=lambda **_: _manifest(prepared),
            exec_fn=lambda file, argv, env: exec_calls.append((file, argv, dict(env))),
            ambient_env={
                "PATH": "/usr/bin:/bin",
                "PLAN_TEST_MODE": "0",
                "PLAN_GATEWAY_ADAPTER": "real",
                "PLAN_FAKE_NOW": "2099-01-01T00:00:00",
            },
        ),
    )

    assert result.exit_code == 0, result.output
    run_env = exec_calls[0][2]
    assert run_env["PLAN_TEST_MODE"] == "1"
    assert run_env["PLAN_GATEWAY_ADAPTER"] == "fake"
    assert run_env["PLAN_FAKE_NOW"] == "2026-07-04T12:00:00+00:00"


def test_run_help_does_not_expose_hidden_test_mode() -> None:
    result = CliRunner().invoke(environment, ["run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--test-mode" not in result.output


def test_prepare_staging_cli_materializes_fake_state(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    prepared_calls: list[dict[str, Any]] = []

    def prepare_instance(**kwargs: Any) -> EnvironmentManifest:
        prepared_calls.append(kwargs)
        instance = resolve_environment_instance(
            kind=kwargs["kind"],
            instance_id=kwargs["instance_id"],
            environment_root=kwargs["environment_root"],
            port=kwargs["port"],
            credentials_env_file=kwargs["credentials_env_file"],
            allowed_repository_roots=kwargs["repository_roots"],
            requested_repository_roots=kwargs["repository_roots"],
            prepared=True,
            fixture_version="fake-fixture-v1",
        )
        return _manifest(instance)

    result = CliRunner().invoke(
        environment,
        [
            "prepare",
            "--kind",
            "staging",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
            "--json",
        ],
        obj=EnvironmentCliDependencies(prepare_instance=prepare_instance),
    )

    assert result.exit_code == 0, result.output
    assert prepared_calls == [
        {
            "kind": "staging",
            "instance_id": None,
            "environment_root": _short_environment_root(tmp_path),
            "port": None,
            "credentials_env_file": None,
            "repository_roots": (repository_root,),
        }
    ]
    payload = json.loads(result.output)
    assert payload["kind"] == "staging"
    assert payload["prepared"] is True
    assert payload["fixture_version"] == "fake-fixture-v1"
    assert payload["db_path"].endswith("/staging/data/planner.db")
    assert "ANTHROPIC_API_KEY" not in result.output


def test_prepare_live_cli_reports_empty_layout_without_fixture(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)

    def prepare_instance(**kwargs: Any) -> EnvironmentManifest:
        instance = resolve_environment_instance(
            kind="live",
            environment_root=kwargs["environment_root"],
            allowed_repository_roots=kwargs["repository_roots"],
            requested_repository_roots=kwargs["repository_roots"],
            prepared=True,
            fixture_version=None,
        )
        return _manifest(instance)

    result = CliRunner().invoke(
        environment,
        [
            "prepare",
            "--kind",
            "live",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
            "--json",
        ],
        obj=EnvironmentCliDependencies(prepare_instance=prepare_instance),
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kind"] == "live"
    assert payload["fixture_version"] is None
    assert payload["prepared"] is True


def test_inspect_json_exposes_paths_but_not_credential_values(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    env_file = tmp_path / "credentials.env"
    env_file.write_text("ANTHROPIC_API_KEY=secret-value", encoding="utf-8")
    inspected = resolve_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9012,
        credentials_env_file=env_file,
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
        prepared=True,
        fixture_version="fake-fixture-v1",
    )

    result = CliRunner().invoke(
        environment,
        [
            "inspect",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
            "--json",
        ],
        obj=EnvironmentCliDependencies(inspect_instance=lambda **_: _manifest(inspected)),
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["kind"] == "preview"
    assert payload["instance_id"] == "feature-123"
    assert payload["credentials_env_file"] == str(env_file.resolve())
    assert payload["expected_linux_account"] == "panels-worker"
    assert payload["repository_roots"] == [str(repository_root.resolve())]
    assert payload["fixture_version"] == "fake-fixture-v1"
    assert payload["prepared"] is True
    assert payload["running"] is False
    assert "secret-value" not in result.output
    assert "ANTHROPIC_API_KEY" not in result.output


def test_inspect_json_fails_clearly_when_running_state_cannot_be_probed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = _repository_root(tmp_path)
    inspected = resolve_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
        prepared=True,
    )

    class FailingLease:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def acquire(self) -> None:
            raise ServerLifecycleError("lease probe failed")

        def release(self) -> None:
            raise AssertionError("release should not be called after failed acquire")

    monkeypatch.setattr(materialize, "PortScopedServerLifecycleLease", FailingLease)

    result = CliRunner().invoke(
        environment,
        [
            "inspect",
            "--kind",
            "staging",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
            "--json",
        ],
        obj=EnvironmentCliDependencies(inspect_instance=lambda **_: _manifest(inspected)),
    )

    assert result.exit_code != 0
    assert "could not probe environment running state" in result.output
    assert "lease probe failed" in result.output


def test_reset_cli_rebuilds_only_non_live_instances(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    reset_calls: list[dict[str, Any]] = []

    def reset_instance(**kwargs: Any) -> EnvironmentManifest:
        reset_calls.append(kwargs)
        instance = resolve_environment_instance(
            kind=kwargs["kind"],
            instance_id=kwargs["instance_id"],
            environment_root=kwargs["environment_root"],
            port=kwargs["port"],
            credentials_env_file=kwargs["credentials_env_file"],
            allowed_repository_roots=kwargs["repository_roots"],
            requested_repository_roots=kwargs["repository_roots"],
            prepared=True,
            fixture_version="fake-fixture-v1",
        )
        return _manifest(instance)

    result = CliRunner().invoke(
        environment,
        [
            "reset",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--port",
            "9012",
            "--repository-root",
            str(repository_root),
            "--json",
        ],
        obj=EnvironmentCliDependencies(reset_instance=reset_instance),
    )

    assert result.exit_code == 0, result.output
    assert reset_calls[0]["kind"] == "preview"
    assert reset_calls[0]["instance_id"] == "feature-123"
    assert json.loads(result.output)["fixture_version"] == "fake-fixture-v1"


def test_preview_inspect_loads_prepared_manifest_without_repeated_options(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    prepared = prepare_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9180,
        credentials_env_file=tmp_path / "preview.env",
        repository_roots=(repository_root,),
    )

    result = CliRunner().invoke(
        environment,
        [
            "inspect",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["port"] == prepared.port
    assert payload["repository_roots"] == [str(repository_root.resolve())]
    assert payload["credentials_env_file"] == str((tmp_path / "preview.env").resolve())


def test_preview_run_loads_prepared_manifest_without_repeated_options(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    prepare_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9181,
        repository_roots=(repository_root,),
    )
    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []

    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
        ],
        obj=EnvironmentCliDependencies(
            exec_fn=lambda file, argv, env: exec_calls.append((file, argv, dict(env))),
            ambient_env={"PATH": "/usr/bin:/bin"},
        ),
    )

    assert result.exit_code == 0, result.output
    assert len(exec_calls) == 1
    run_env = exec_calls[0][2]
    assert run_env["PLAN_PORT"] == "9181"
    assert run_env["PLAN_DB_PATH"].endswith("/previews/feature-123/data/planner.db")


def test_run_rejects_manifest_repository_root_tampering_before_chdir_or_exec(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    caller_disallowed_repository_root = tmp_path / "caller-disallowed-repo"
    caller_disallowed_repository_root.mkdir()
    (caller_disallowed_repository_root / ".git").mkdir()
    prepared = prepare_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9188,
        repository_roots=(repository_root,),
    )
    payload = json.loads((prepared.instance_root / "manifest.json").read_text(encoding="utf-8"))
    payload["repository_roots"] = [str(caller_disallowed_repository_root.resolve())]
    (prepared.instance_root / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []
    cwd_before = Path.cwd()

    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
        ],
        obj=EnvironmentCliDependencies(
            exec_fn=lambda file, argv, env: exec_calls.append((file, argv, dict(env))),
            ambient_env={"PATH": "/usr/bin:/bin"},
        ),
    )

    assert result.exit_code != 0
    assert "repository root is not allowed" in result.output
    assert exec_calls == []
    assert Path.cwd() == cwd_before


def test_run_rejects_tampered_manifest_before_exec(tmp_path: Path) -> None:
    repository_root = _repository_root(tmp_path)
    prepared = prepare_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9187,
        repository_roots=(repository_root,),
    )
    payload = json.loads((prepared.instance_root / "manifest.json").read_text(encoding="utf-8"))
    payload["hermes_home"] = str(tmp_path / "outside-hermes-home")
    (prepared.instance_root / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    exec_calls: list[tuple[str, list[str], dict[str, str]]] = []

    result = CliRunner().invoke(
        environment,
        [
            "run",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--repository-root",
            str(repository_root),
        ],
        obj=EnvironmentCliDependencies(
            exec_fn=lambda file, argv, env: exec_calls.append((file, argv, dict(env))),
            ambient_env={"PATH": "/usr/bin:/bin"},
        ),
    )

    assert result.exit_code != 0
    assert "manifest" in result.output
    assert exec_calls == []


def test_preview_reset_cli_loads_actual_prepared_port_for_lifecycle_safety(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    prepared = prepare_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9182,
        repository_roots=(repository_root,),
    )

    result = CliRunner().invoke(
        environment,
        [
            "reset",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["port"] == prepared.port
    assert payload["hermes_home"] == str(prepared.hermes_home)


def test_remove_cli_removes_prepared_preview_without_repeated_options(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    prepared = prepare_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9183,
        repository_roots=(repository_root,),
    )

    result = CliRunner().invoke(
        environment,
        [
            "remove",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["instance_root"] == str(prepared.instance_root)
    assert not prepared.instance_root.exists()


def test_render_linux_cli_reads_prepared_manifest_without_exposing_credentials(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    credentials_env_file = tmp_path / "preview.env"
    credentials_env_file.write_text("ANTHROPIC_API_KEY=secret-value\n", encoding="utf-8")
    prepared = prepare_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=_short_environment_root(tmp_path),
        port=9184,
        credentials_env_file=credentials_env_file,
        repository_roots=(repository_root,),
    )

    result = CliRunner().invoke(
        environment,
        [
            "render-linux",
            "--kind",
            "preview",
            "--instance-id",
            "feature-123",
            "--environment-root",
            str(_short_environment_root(tmp_path)),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "panels-preview-feature-123.service" in result.output
    assert "User=panels-worker" in result.output
    assert "panels environment run --kind preview" in result.output
    assert "--instance-id feature-123" in result.output
    assert str(prepared.credentials_env_file) in result.output
    assert "secret-value" not in result.output


def test_smoke_hermes_cli_uses_prepared_staging_and_preview_manifests(
    tmp_path: Path,
) -> None:
    staging_repository_root = _repository_root(tmp_path, "staging-repo")
    preview_repository_root = _repository_root(tmp_path, "preview-repo")
    environment_root = _short_environment_root(tmp_path)
    prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        port=9185,
        repository_roots=(staging_repository_root,),
    )
    preview = prepare_environment_instance(
        kind="preview",
        instance_id="feature-123",
        environment_root=environment_root,
        port=9186,
        repository_roots=(preview_repository_root,),
    )
    smoke_calls: list[dict[str, Any]] = []

    def smoke_instances(**kwargs: Any) -> object:
        smoke_calls.append(kwargs)

        class Report:
            staging_stored_session_id = "stored-staging"
            preview_stored_session_id = "stored-preview"
            staging_home = environment_root / "staging" / "hermes-home"
            preview_home = preview.hermes_home

        return Report()

    result = CliRunner().invoke(
        environment,
        [
            "smoke-hermes",
            "--environment-root",
            str(environment_root),
            "--preview-id",
            "feature-123",
            "--hermes-python",
            str(tmp_path / "hermes-python"),
            "--json",
        ],
        obj=EnvironmentCliDependencies(smoke_instances=smoke_instances),
    )

    assert result.exit_code == 0, result.output
    assert len(smoke_calls) == 1
    assert smoke_calls[0]["hermes_python"] == tmp_path / "hermes-python"
    assert smoke_calls[0]["ambient_env"] is None
    assert smoke_calls[0]["staging"].kind == "staging"
    assert smoke_calls[0]["preview"].kind == "preview"
    payload = json.loads(result.output)
    assert payload == {
        "preview_home": str(preview.hermes_home),
        "preview_stored_session_id": "stored-preview",
        "staging_home": str(environment_root / "staging" / "hermes-home"),
        "staging_stored_session_id": "stored-staging",
    }


def _manifest(instance, *, prepared_at: int | None = 1_800_000_000) -> EnvironmentManifest:  # type: ignore[no-untyped-def]
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


def _repository_root(tmp_path: Path, name: str = "repo") -> Path:
    repository_root = tmp_path / name
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    return repository_root


def _short_environment_root(tmp_path: Path) -> Path:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    return Path("/tmp") / f"pe-cli-{os.getpid()}-{digest}"
