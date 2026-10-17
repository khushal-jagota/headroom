"""Executable wiring instruments; none asserts Mac/account runtime proof."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from planner.environments.resident import cli, driver, transport
from planner.environments.resident.contracts import (
    ExecutorLaunchOptions,
    ExecutorSourceClosure,
    HostDeploymentResult,
    ResidentError,
    ResidentPackage,
)
from planner.environments.resident.native_builder import NativeRuntimeBuilder
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION

SHA = "a" * 40


def _package() -> ResidentPackage:
    return ResidentPackage(
        SHA,
        PROTOCOL_VERSION,
        SPOOL_FORMAT_VERSION,
        "darwin",
        "arm64",
        "3.12",
        ExecutorSourceClosure(
            ("src/example/runner.py", "requirements.lock"), "example.runner", "requirements.lock"
        ),
    )


def _remote(tmp_path: Path) -> dict[str, Any]:
    key, hosts, spec = tmp_path / "key", tmp_path / "hosts", tmp_path / "spec"
    key.write_text("fixture-key")
    key.chmod(0o600)
    hosts.write_text("mac ssh-ed25519 fixture-pinned-key\n")
    spec.write_text(json.dumps(asdict(_package())))
    return dict(
        source=tmp_path,
        manifest=spec,
        host="owner@mac",
        known_hosts=hosts,
        identity=key,
        python=Path("/opt/python/bin/python3"),
        launch=ExecutorLaunchOptions("vps", SHA, "https://panels.example"),
        timeout_seconds=1200.0,
    )


def test_remote_authenticated_transfer_and_exact_host_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _remote(tmp_path)
    adapter = transport.SshResidentDeployment(**config)
    monkeypatch.setattr(transport, "build_delivery_archive", lambda *args: b"source-tar")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                asdict(HostDeploymentResult("mac", SHA, "verified", SHA, "Exact executor ready"))
            ).encode(),
            b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert adapter.deploy(SHA).disposition == "verified"
    argv, kwargs = calls[0]
    assert "StrictHostKeyChecking=yes" in argv
    assert "GlobalKnownHostsFile=/dev/null" in argv
    assert f"UserKnownHostsFile={config['known_hosts']}" in argv
    assert argv[argv.index("-i") + 1] == str(config["identity"])
    assert "IdentitiesOnly=yes" in argv
    assert "-F" in argv and "/dev/null" in argv
    assert kwargs["input"] == b"source-tar"
    assert kwargs["timeout"] == 1200.0
    assert len(calls) == 1


@pytest.mark.parametrize("outcome", ["disconnect", "timeout", "lying-report"])
def test_remote_unverified_is_not_success_or_queued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    adapter = transport.SshResidentDeployment(**_remote(tmp_path))
    monkeypatch.setattr(transport, "build_delivery_archive", lambda *args: b"source-tar")

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        code = 255 if outcome == "disconnect" else 0
        return subprocess.CompletedProcess(
            argv,
            code,
            json.dumps(
                asdict(
                    HostDeploymentResult("mac", SHA, "verified", "b" * 40, "Wrong host revision")
                )
            ).encode(),
            b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    if outcome == "lying-report":
        with pytest.raises(ResidentError, match="no valid exact-revision"):
            adapter.deploy(SHA)
    else:
        result = adapter.deploy(SHA)
        assert result.disposition == "unreachable"
        assert result.observed_revision is None


def test_preflight_validates_complete_config_and_archive_without_host_contact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _remote(tmp_path)
    calls = []
    monkeypatch.setattr(transport, "build_delivery_archive", lambda *args: calls.append(args))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("preflight must not contact a host")

    monkeypatch.setattr(transport.SshResidentDeployment, "deploy", forbidden)
    args = [
        "preflight",
        "--requested-sha",
        SHA,
        "--source-root",
        str(tmp_path),
        "--manifest",
        str(config["manifest"]),
        "--ssh-host",
        "owner@mac",
        "--known-hosts",
        str(config["known_hosts"]),
        "--identity",
        str(config["identity"]),
        "--python",
        "/opt/python/bin/python3",
        "--executor-ssh-host",
        "vps",
        "--panels-server-url",
        "https://panels.example",
    ]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["disposition"] == "prepared"
    assert len(calls) == 1
    config["identity"].chmod(0o644)
    assert cli.main(args) == 2
    assert "owner-only" in capsys.readouterr().out


def test_native_prepare_reexecutes_new_runtime_with_explicit_tool_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    app = tmp_path / "app"
    app.mkdir()
    (app / "resident-package.json").write_text(json.dumps({"package": asdict(_package())}))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: home)
    runtime = tmp_path / "permanent-runtime"
    prepared = SimpleNamespace(runtime=runtime)
    built = []

    def prepare(app: Path, package: Any, paths: Any, builder: Any) -> Any:
        assert isinstance(builder, NativeRuntimeBuilder)
        assert builder.python == Path("/native/python")
        built.append((app, paths))
        return prepared

    monkeypatch.setattr(driver, "prepare_runtime", prepare)

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == str(runtime / "bin/python")
        assert argv[1:4] == ["-I", "-B", "-c"]
        assert "activate" in argv
        assert argv[argv.index("--backend-path") + 1] == "/native/bin:/usr/bin"
        assert kwargs["timeout"] == 120.0
        assert "PYTHONPATH" not in kwargs["env"]
        assert "PLAN_DB_PATH" not in kwargs["env"]
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                asdict(HostDeploymentResult("mac", SHA, "verified", SHA, "Instrument ready"))
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("PLAN_DB_PATH", "/do-not-inherit")
    result = driver.prepare_and_activate(
        app,
        python=Path("/native/python"),
        home=home,
        launch=ExecutorLaunchOptions(
            "vps", SHA, "https://panels.example", backend_path="/native/bin:/usr/bin"
        ),
        timeout_seconds=30.0,
    )
    assert result.disposition == "verified"
    assert len(built) == 1


def test_reports_partial_results_even_when_vps_health_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("unavailable")

    monkeypatch.setattr(urllib.request, "urlopen", fail)

    class Mac:
        def deploy(self, revision: str) -> HostDeploymentResult:
            assert (tmp_path / "report").exists()  # durable pending record before host mutation
            return HostDeploymentResult("mac", revision, "verified", revision, "Instrument ready")

    record = driver.deploy_and_record_remote(
        Mac(), SHA, "http://127.0.0.1:8767/api/health", tmp_path / "report", "run-1"
    )
    assert record.result.mac.disposition == "verified"
    assert record.result.vps.disposition == "failed"
    assert record.result.verified is False


def test_production_package_spec_uses_exact_pinned_source_without_builds(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    output = tmp_path / "spec"
    driver.write_package_spec(
        source,
        revision,
        source / "ops/panels-environments/executor-build-contract.json",
        "arm64",
        "3.12",
        output,
    )
    package = json.loads(output.read_bytes())
    assert package["application_revision"] == revision
    assert package["protocol_version"] == PROTOCOL_VERSION
    assert package["spool_format_version"] == SPOOL_FORMAT_VERSION
    closure = package["closure"]
    assert closure["entrypoint_module"] == "planner.executor.entrypoint"
    assert "src/planner/executor/preflight.py" in closure["files"]
    assert "src/planner/core/server.py" in closure["files"]
    assert closure["node_package_roots"] == ["agent_backends"]
    assert not any(name.startswith(("web/", "data/", ".venv/")) for name in closure["files"])
    assert set(tmp_path.iterdir()) == {output}


def test_real_adapter_wiring_retries_same_interrupted_first_install_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    import time

    from planner.environments.resident import control, packaging, service
    from planner.environments.resident.paths import resident_paths
    from planner.executor.contracts import ConnectionState, ExecutorHealth, ExecutorIdentity

    home = tmp_path / "owner"
    home.mkdir()
    source = tmp_path / "source"
    (source / "src/example").mkdir(parents=True)
    (source / "src/example/runner.py").write_text("pass\n")
    (source / "requirements.lock").write_text("")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    from dataclasses import replace

    package = replace(_package(), application_revision=revision)
    paths = resident_paths(home)
    app = packaging.build_source_package(source, package, paths.deployment_root / "staged-1")

    def builder(app: Path, runtime: Path, package: ResidentPackage) -> None:
        (runtime / "bin").mkdir()
        (runtime / "bin/python").symlink_to(sys.executable)

    packaging.prepare_runtime(app, package, paths, builder)
    real_control, real_service = control.OwnerExecutorControl, service.LaunchAgentService
    instances = []

    def owner(*args: Any, **kwargs: Any) -> Any:
        adapter = real_control(*args, **kwargs)
        assert isinstance(adapter, real_control)

        def observe() -> ExecutorHealth | None:
            if not started[0]:
                return None
            return ExecutorHealth(
                ExecutorIdentity(
                    PROTOCOL_VERSION, revision, SPOOL_FORMAT_VERSION, "fixture-incarnation", 1
                ),
                123,
                ConnectionState.ready,
                True,
                time.time_ns() // 1_000_000,
                None,
            )

        adapter.observe = observe  # type: ignore[method-assign]
        adapter.wait_for_health = lambda *args, **kwargs: observe()  # type: ignore[method-assign,assignment]
        instances.append(adapter)
        return adapter

    started, bootstraps = [False], [0]
    calls = []

    def commands(argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1] == "print":
            return subprocess.CompletedProcess(argv, 113, "", "Could not find service")
        assert argv[1] == "bootstrap"
        bootstraps[0] += 1
        if bootstraps[0] == 1:
            return subprocess.CompletedProcess(argv, 1, "", "instrumented bootstrap refusal")
        started[0] = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def launch(*args: Any, **kwargs: Any) -> Any:
        adapter = real_service(*args, **kwargs, run=commands)
        assert isinstance(adapter, real_service)
        return adapter

    monkeypatch.setattr(control, "OwnerExecutorControl", owner)
    monkeypatch.setattr(service, "LaunchAgentService", launch)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: home)
    launch_options = ExecutorLaunchOptions("vps", revision, "https://panels.example")
    with pytest.raises(ResidentError, match="bootstrap failed"):
        driver.activate(app, home=home, launch=launch_options, timeout_seconds=1.0)
    assert paths.app.is_dir()
    assert (paths.deployment_root / "initial-install.json").exists()
    retry = paths.deployment_root / "staged-2"
    shutil.copytree(paths.app, retry)
    result = driver.activate(retry, home=home, launch=launch_options, timeout_seconds=1.0)
    assert result.disposition == "verified"
    assert not (paths.deployment_root / "initial-install.json").exists()
    assert bootstraps == [2]
    assert all(adapter.before_quiesce is not None for adapter in instances)
    assert all(command[1] in {"print", "bootstrap"} for command in calls)
