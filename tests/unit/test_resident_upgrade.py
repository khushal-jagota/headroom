"""Unsupported protocol transitions refuse before deployed host mutation."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.support.resident_runtime import write_runtime_identity

from planner.environments.app import AppManifest, digest_app_artifact
from planner.environments.resident import cli, driver, packaging, transport, update
from planner.environments.resident.contracts import (
    ExecutorLaunchOptions,
    ExecutorSourceClosure,
    HostName,
    InstalledExecutorCompatibility,
    PreparedResidentPackage,
    ResidentError,
    ResidentPackage,
    ResidentPaths,
)
from planner.environments.resident.installed import installed_identity
from planner.environments.resident.logic.upgrade import require_supported_upgrade
from planner.environments.resident.preflight import (
    CONFIGURATION_SOURCE,
    inspect_installed_mac,
    inspect_installed_vps,
    mac_admission_source,
    mac_probe_command_source,
    parse_mac_identity,
    requested_versions,
    supported_versions,
    versions_from_source,
)
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import SshAuthenticationMode

SHA = "a" * 40


def _launch(root: Path, revision: str) -> ExecutorLaunchOptions:
    key, hosts = root / "executor-key", root / "executor-hosts"
    key.write_text("fixture-private-key")
    key.chmod(0o600)
    hosts.write_text("vps.example ssh-ed25519 fixture-pinned-host-key\n")
    return ExecutorLaunchOptions(
        "vps.example",
        revision,
        "https://panels.example",
        2222,
        "owner",
        key,
        hosts,
        SshAuthenticationMode.public_key,
    )


def _source(protocol: int = PROTOCOL_VERSION) -> str:
    return f"PROTOCOL_VERSION: Final = {protocol}\nSPOOL_FORMAT_VERSION = {SPOOL_FORMAT_VERSION}\n"


def _mac(home: Path, protocol: int = PROTOCOL_VERSION) -> Path:
    app = home / "Deployments/PanelsExecutor/current/app"
    path = app / CONFIGURATION_SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(_source(protocol))
    (app / "resident-package.json").write_text(
        json.dumps(
            {
                "package": {
                    "application_revision": SHA,
                    "protocol_version": protocol,
                    "spool_format_version": SPOOL_FORMAT_VERSION,
                },
                "files": {CONFIGURATION_SOURCE: hashlib.sha256(path.read_bytes()).hexdigest()},
            }
        )
    )
    digest = hashlib.sha256((app / "resident-package.json").read_bytes()).hexdigest()
    runtime = home / "Deployments/PanelsExecutor/runtimes" / digest
    (runtime / "bin").mkdir(parents=True)
    write_runtime_identity(runtime)
    (runtime / "resident-runtime.json").write_text(
        json.dumps(
            {
                "input_digest": digest,
                "runtime_path": str(runtime.absolute()),
                "installed_identity": installed_identity(runtime),
            }
        )
    )
    return app


def _vps(app: Path, protocol: int = PROTOCOL_VERSION) -> None:
    config = app / CONFIGURATION_SOURCE
    config.parent.mkdir(parents=True)
    config.write_text(_source(protocol))
    manifest = AppManifest(SHA, "0" * 64, digest_app_artifact(app))
    (app / "manifest.json").write_text(json.dumps(manifest.as_dict()))


def _git(source: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *arguments], text=True).strip()


def _commit(source: Path) -> str:
    _git(source, "add", ".")
    _git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return _git(source, "rev-parse", "HEAD")


def _repository(source: Path) -> str:
    (source / CONFIGURATION_SOURCE).parent.mkdir(parents=True)
    (source / CONFIGURATION_SOURCE).write_text(_source())
    (source / "requirements.lock").write_text("")
    (source / "requirements-build.txt").write_text("")
    (source / "src/example").mkdir()
    (source / "src/example/runner.py").write_text("print('exact old app')\n")
    _git(source, "init", "-q")
    return _commit(source)


def _package(revision: str) -> ResidentPackage:
    return ResidentPackage(
        revision,
        PROTOCOL_VERSION,
        SPOOL_FORMAT_VERSION,
        "darwin",
        "arm64",
        "3.14.3",
        ExecutorSourceClosure(
            (
                CONFIGURATION_SOURCE,
                "src/example/runner.py",
                "requirements.lock",
                "requirements-build.txt",
            ),
            "example.runner",
            "requirements.lock",
        ),
    )


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


@pytest.mark.parametrize(
    "source",
    [
        "PROTOCOL_VERSION=True\nSPOOL_FORMAT_VERSION=1",
        "PROTOCOL_VERSION=2+2\nSPOOL_FORMAT_VERSION=1",
        "PROTOCOL_VERSION=4\nPROTOCOL_VERSION=4\nSPOOL_FORMAT_VERSION=1",
        "PROTOCOL_VERSION=0\nSPOOL_FORMAT_VERSION=1",
        "PROTOCOL_VERSION=4\nSPOOL_FORMAT_VERSION=1\nPROTOCOL_VERSION += 1",
        "PROTOCOL_VERSION=4\nSPOOL_FORMAT_VERSION=1\nif True: PROTOCOL_VERSION=3",
        "PROTOCOL_VERSION=4",
        "not Python !",
    ],
)
def test_unknown_candidate_versions_refuse(source: str) -> None:
    with pytest.raises(ResidentError):
        versions_from_source(source)


def test_versions_read_exact_git_commit_and_current_tooling_stays_separate(tmp_path: Path) -> None:
    source = tmp_path / "repository"
    earlier = _repository(source)
    (source / "src/example/runner.py").write_text("print('later app')\n")
    _commit(source)
    package = _package(earlier)
    manifest = tmp_path / "manifest"
    manifest.write_text(json.dumps(asdict(package)))
    archive = transport.build_delivery_archive(source, manifest, earlier)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        app = bundle.extractfile("app/src/example/runner.py")
        tools = bundle.extractfile("tools/src/planner/environments/resident/preflight.py")
        assert app is not None and app.read() == b"print('exact old app')\n"
        assert tools is not None
        assert tools.read() == Path(driver.__file__).with_name("preflight.py").read_bytes()
    (source / CONFIGURATION_SOURCE).write_text(_source(PROTOCOL_VERSION - 1))
    incompatible = _commit(source)
    assert requested_versions(source, earlier) == supported_versions()
    assert requested_versions(source, incompatible).protocol_version == PROTOCOL_VERSION - 1
    forged = replace(package, application_revision=incompatible)
    with pytest.raises(ResidentError, match="exact source"):
        packaging.build_source_package(source, forged, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()
    # Production package-spec must use the requested constants, not tooling's.
    build_contract = tmp_path / "build-contract"
    build_contract.write_text(
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("ops/panels-environments/executor-build-contract.json")
        .read_text()
    )
    # Replace only ancestry proof for the deliberately independent synthetic repo.
    # The actual requested-source parser and package validator run unchanged.
    from unittest.mock import patch

    real_run = subprocess.run

    def run(argv: list[str], **kwargs: Any) -> Any:
        if "merge-base" in argv:
            return subprocess.CompletedProcess(argv, 0)
        return real_run(argv, **kwargs)

    with patch.object(subprocess, "run", run), pytest.raises(ResidentError, match="compatibility"):
        driver.write_package_spec(
            source, incompatible, build_contract, "arm64", "3.14.3", tmp_path / "output"
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("host", ["vps", "mac"])
def test_each_host_protocol_change_refuses_even_with_equal_spool(host: HostName) -> None:
    supported = supported_versions()
    installed = InstalledExecutorCompatibility(
        host, SHA, replace(supported, protocol_version=PROTOCOL_VERSION - 1)
    )
    with pytest.raises(ResidentError, match=host):
        require_supported_upgrade(supported, (installed,), supported)
    require_supported_upgrade(
        supported,
        (
            replace(installed, application_revision="b" * 40, versions=supported),
            InstalledExecutorCompatibility("mac", None, None),
        ),
        supported,
    )


@pytest.mark.parametrize(
    "evidence",
    [
        "initial-install.json",
        "spool",
        "plist",
        "missing-manifest",
        "malformed",
        "bad-hash",
        "bad-versions",
        "dangling",
    ],
)
def test_unknown_mac_install_is_never_first_install(tmp_path: Path, evidence: str) -> None:
    home = tmp_path / "owner"
    home.mkdir()
    if evidence in {"missing-manifest", "malformed", "bad-hash", "bad-versions"}:
        app = _mac(home)
        manifest = app / "resident-package.json"
        if evidence == "missing-manifest":
            manifest.unlink()
        elif evidence == "malformed":
            manifest.write_text("[]")
        elif evidence == "bad-hash":
            (app / CONFIGURATION_SOURCE).write_text(_source(PROTOCOL_VERSION - 1))
        else:
            payload = json.loads(manifest.read_bytes())
            payload["package"]["protocol_version"] = True
            manifest.write_text(json.dumps(payload))
    elif evidence == "dangling":
        app = home / "Deployments/PanelsExecutor/current/app"
        app.parent.mkdir(parents=True)
        app.symlink_to(home / "absent")
    else:
        path = {
            "initial-install.json": "Deployments/PanelsExecutor/initial-install.json",
            "spool": "Library/Application Support/Panels/executor/spool/metadata.json",
            "plist": "Library/LaunchAgents/com.panels.executor.plist",
        }[evidence]
        target = home / path
        target.parent.mkdir(parents=True)
        target.write_text("existing evidence")
    before = _snapshot(home)
    with pytest.raises(ResidentError):
        inspect_installed_mac(home)
    assert _snapshot(home) == before


def test_confirmed_first_and_interrupted_installs_and_managed_vps_symlinks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    assert inspect_installed_mac(home).versions is None
    app = tmp_path / "vps/current/app"
    assert inspect_installed_vps(app).versions is None
    _mac(home)
    marker = home / "Deployments/PanelsExecutor/initial-install.json"
    marker.write_text("existing interrupted installation remains for activate to evaluate")
    assert inspect_installed_mac(home).versions == supported_versions()
    real = tmp_path / "vps/releases/current-app"
    _vps(real)
    app.parent.mkdir(parents=True)
    app.symlink_to(real, target_is_directory=True)
    assert inspect_installed_vps(app).versions == supported_versions()
    (real / "manifest.json").write_text("malformed")
    with pytest.raises(ResidentError):
        inspect_installed_vps(app)


def test_actual_probe_and_bootstrap_refuse_without_any_filesystem_mutation(tmp_path: Path) -> None:
    home = tmp_path / "owner"
    home.mkdir()
    _mac(home, PROTOCOL_VERSION - 1)
    before = _snapshot(home)
    probe = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", mac_probe_command_source()],
        env=dict(os.environ, HOME=str(home)),
        capture_output=True,
        check=True,
    )
    identity = parse_mac_identity(probe.stdout)
    assert identity.versions is not None
    assert identity.versions.protocol_version == PROTOCOL_VERSION - 1
    # This is the exact source composition used for mutation delivery in transport.
    bootstrap = mac_admission_source(supported_versions()) + "\n" + transport._REMOTE_BOOTSTRAP
    failed = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", bootstrap, "W10="],
        env=dict(os.environ, HOME=str(home)),
        input=b"not even an archive",
        capture_output=True,
    )
    assert failed.returncode != 0
    assert b"protocol/spool transition refused" in failed.stderr
    assert _snapshot(home) == before
    assert not (home / "Deployments/PanelsExecutor/staging").exists()


def test_direct_local_paths_refuse_before_source_or_native_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "owner"
    home.mkdir()
    _mac(home, PROTOCOL_VERSION - 1)
    source = tmp_path / "repository"
    revision = _repository(source)
    package = _package(revision)
    manifest = tmp_path / "spec"
    manifest.write_text(json.dumps(asdict(package)))
    candidate = packaging.build_source_package(source, package, tmp_path / "prepared")
    launch = _launch(tmp_path, revision)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: home)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("native/source build must not begin")

    monkeypatch.setattr(driver, "prepare_runtime", forbidden)
    monkeypatch.setattr(driver, "build_source_package", forbidden)
    before = _snapshot(home)
    with pytest.raises(ResidentError, match="transition refused"):
        driver.prepare_and_activate(
            candidate, python=Path(sys.executable), home=home, launch=launch, timeout_seconds=1
        )
    with pytest.raises(ResidentError, match="transition refused"):
        driver.deploy_local_source(
            source,
            manifest,
            tmp_path / "new-candidate",
            python=Path(sys.executable),
            home=home,
            launch=launch,
            timeout_seconds=1,
        )
    assert not (tmp_path / "new-candidate").exists()
    assert _snapshot(home) == before


def test_direct_update_refuses_before_lock_path_or_service_mutation(tmp_path: Path) -> None:
    package = _package(SHA)
    paths = ResidentPaths(tmp_path, tmp_path / "deployment", tmp_path / "persistent")
    previous = PreparedResidentPackage(
        replace(package, protocol_version=PROTOCOL_VERSION - 1),
        paths.app,
        paths.runtimes / "previous",
    )
    candidate = PreparedResidentPackage(package, tmp_path / "candidate", paths.runtimes / "next")

    class Forbidden:
        def __getattr__(self, name: str) -> Any:
            pytest.fail(f"update must refuse before {name}")

    def forbidden_validator(package: PreparedResidentPackage) -> None:
        pytest.fail("update must refuse before package validation")

    result = update.update_resident(
        paths,
        candidate,
        previous,
        control=Forbidden(),
        service=Forbidden(),
        validate_package=forbidden_validator,
        now_ms=lambda: 0,
    )
    assert result.disposition == "failed"
    assert "transition refused" in result.detail
    assert not paths.deployment_root.exists()


@pytest.mark.parametrize("mismatch", ["vps", "mac", "disabled"])
def test_production_workflow_shell_refuses_before_lifecycle_and_vps_mutation(
    tmp_path: Path,
    mismatch: str,
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((checkout / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    admission = next(step for step in steps if step.get("id") == "resident-preflight")
    assert admission["if"] == "vars.PANELS_RESIDENT_DEPLOY_ENABLED == 'true'"
    home = tmp_path / "home"
    home.mkdir()
    vps = home / "Deployments/Panels/current/app"
    _vps(vps, PROTOCOL_VERSION - 1 if mismatch == "vps" else PROTOCOL_VERSION)
    key, hosts = tmp_path / "key", tmp_path / "hosts"
    key.write_text("fixture-private-key")
    key.chmod(0o600)
    hosts.write_text("mac ssh-ed25519 fixture-key\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ssh = binaries / "ssh"
    ssh.write_text(
        "#!/usr/bin/env python3\nimport json\nprint(json.dumps("
        + repr(
            {
                "host": "mac",
                "application_revision": SHA,
                "versions": {
                    "protocol_version": PROTOCOL_VERSION - 1
                    if mismatch == "mac"
                    else PROTOCOL_VERSION,
                    "spool_format_version": SPOOL_FORMAT_VERSION,
                },
            }
        )
        + "))\n"
    )
    ssh.chmod(0o755)
    environment = dict(
        os.environ,
        HOME=str(home),
        PATH=f"{binaries}:{os.environ['PATH']}",
        GITHUB_WORKSPACE=str(checkout),
        PANELS_REQUESTED_SHA=_git(checkout, "rev-parse", "HEAD"),
        PANELS_EXECUTOR_CONTRACT=str(tmp_path / "contract"),
        PANELS_RESIDENT_MANIFEST=str(tmp_path / "manifest"),
        PANELS_MAC_HOST="owner@mac",
        PANELS_MAC_KNOWN_HOSTS=str(hosts),
        PANELS_MAC_SSH_IDENTITY=str(key),
        PANELS_MAC_PYTHON=sys.executable,
        PANELS_MAC_NODE="/usr/bin/node",
        PANELS_MAC_NPM="/usr/bin/npm",
        PANELS_MAC_ARCHITECTURE="arm64",
        PANELS_MAC_PYTHON_VERSION="3.14.3",
        PANELS_EXECUTOR_SSH_HOST="vps.example",
        PANELS_EXECUTOR_SSH_PORT="2222",
        PANELS_EXECUTOR_SSH_USER="owner",
        PANELS_EXECUTOR_SSH_AUTH_MODE="public_key",
        PANELS_EXECUTOR_TAILSCALE_BINARY="",
        PANELS_EXECUTOR_SSH_IDENTITY_FILE=str(tmp_path / "executor-key"),
        PANELS_EXECUTOR_SSH_KNOWN_HOSTS_FILE=str(tmp_path / "executor-hosts"),
        PANELS_EXECUTOR_SERVER_URL="https://panels.example",
        PANELS_EXECUTOR_RELAY_COMMAND="panels executor relay",
        PANELS_EXECUTOR_BACKEND_PATH="",
    )
    # Execute the actual admission, lifecycle and app-deploy shells in their
    # workflow order. Only the deploy runtime is a mutation sentinel executable.
    mutation = tmp_path / "first-host-mutation"
    deploy_runtime = tmp_path / "deploy-runtime/bin"
    deploy_runtime.mkdir(parents=True)
    deploy_python = deploy_runtime / "python"
    deploy_python.write_text(f"#!/bin/sh\ntouch '{mutation}'\n")
    deploy_python.chmod(0o755)
    lifecycle = home / "deployment-lifecycle.json"
    environment.update(
        PANELS_DEPLOY_VENV=str(deploy_runtime.parent),
        PANELS_CANDIDATE_APP=str(tmp_path / "candidate"),
        PANELS_VPS_LAUNCH_CONFIG=str(tmp_path / "launch.json"),
        PANELS_LIFECYCLE_PATH=str(lifecycle),
        PANELS_DEPLOYMENT_ID="synthetic-upgrade",
    )
    script = "set -e\n"
    for step in steps:
        if step.get("id") == "resident-preflight":
            if mismatch != "disabled":
                script += step["run"] + "\n"
        elif step.get("id") == "lifecycle-start" or step.get("name") == "Deploy exact app":
            script += step["run"] + "\n"
    before = _snapshot(home)
    completed = subprocess.run(
        ["bash", "-c", script], env=environment, capture_output=True, text=True
    )
    if mismatch == "disabled":
        assert completed.returncode == 0, completed.stderr
        assert mutation.exists()
        assert lifecycle.exists()
        assert not (tmp_path / "manifest").exists()
    else:
        assert completed.returncode != 0, completed.stdout + completed.stderr
        assert "transition refused" in completed.stdout
        assert not mutation.exists()
        assert not lifecycle.exists()
        assert _snapshot(home) == before


@pytest.mark.parametrize("failure", ["vps", "mac", "bad-json", "disconnect", "timeout"])
def test_remote_cli_refuses_before_report_or_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    source = tmp_path / "source"
    revision = _repository(source)
    manifest = tmp_path / "spec"
    manifest.write_text(json.dumps(asdict(_package(revision))))
    key, hosts = tmp_path / "key", tmp_path / "hosts"
    key.write_text("fixture-key")
    key.chmod(0o600)
    hosts.write_text("mac ssh-ed25519 pinned-fixture-key\n")
    app = tmp_path / "vps/current/app"
    _vps(app, PROTOCOL_VERSION - 1 if failure == "vps" else PROTOCOL_VERSION)
    real_run = subprocess.run
    probes = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        if argv[0] != "ssh":
            return real_run(argv, **kwargs)
        assert "input" not in kwargs, "refusal must precede archive upload"
        probes.append(argv)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        output = (
            b"bad JSON"
            if failure == "bad-json"
            else json.dumps(
                {
                    "host": "mac",
                    "application_revision": SHA,
                    "versions": {
                        "protocol_version": PROTOCOL_VERSION - 1
                        if failure == "mac"
                        else PROTOCOL_VERSION,
                        "spool_format_version": SPOOL_FORMAT_VERSION,
                    },
                }
            ).encode()
        )
        return subprocess.CompletedProcess(argv, 255 if failure == "disconnect" else 0, output, b"")

    monkeypatch.setattr(subprocess, "run", run)
    report = tmp_path / "records/report"
    result = cli.main(
        [
            "deploy-remote",
            "--vps-app",
            str(app),
            "--requested-sha",
            revision,
            "--source-root",
            str(source),
            "--manifest",
            str(manifest),
            "--ssh-host",
            "owner@mac",
            "--known-hosts",
            str(hosts),
            "--identity",
            str(key),
            "--python",
            sys.executable,
            "--executor-ssh-host",
            "vps.example",
            "--executor-ssh-port",
            "2222",
            "--executor-ssh-auth-mode",
            "public_key",
            "--executor-ssh-user",
            "owner",
            "--executor-ssh-identity-file",
            str(tmp_path / "executor-key"),
            "--executor-ssh-known-hosts",
            str(tmp_path / "executor-hosts"),
            "--panels-server-url",
            "https://panels.example",
            "--vps-health-url",
            "http://127.0.0.1:8767/api/health",
            "--report",
            str(report),
            "--deployment-id",
            "test",
        ]
    )
    assert result == 2
    assert "error" in json.loads(capsys.readouterr().out)
    assert not report.parent.exists()
    assert probes  # Every complete inspection uses pinned SSH, even on VPS mismatch.
    assert "StrictHostKeyChecking=yes" in probes[0]


@pytest.mark.parametrize("evidence", ["data", "malformed", "missing", "dangling"])
def test_unknown_vps_install_is_not_first_install(tmp_path: Path, evidence: str) -> None:
    app = tmp_path / "current/app"
    app.parent.mkdir(parents=True)
    if evidence == "data":
        (app.parent / "data").mkdir()
    elif evidence == "dangling":
        app.symlink_to(tmp_path / "missing")
    else:
        _vps(app)
        manifest = app / "manifest.json"
        if evidence == "missing":
            manifest.unlink()
        else:
            manifest.write_text("[]")
    with pytest.raises(ResidentError, match="unknown"):
        inspect_installed_vps(app)
