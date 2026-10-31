"""Admission risks absent from the synthetic deployment transaction fixtures."""

from __future__ import annotations

import ctypes
import json
import platform
import shlex
import shutil
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from tests.unit.test_deployment import SHA_A, SHA_B, FakeHealth, FakeService, _app
from tests.unit.test_owner_updates import release

from planner.environments import runtime_admission as admission
from planner.environments.app import AppValidationError, digest_app_artifact, validate_app_manifest
from planner.environments.deployment import DeploymentError, deploy_app
from planner.environments.update_requests import parse_release


@pytest.mark.parametrize("first_install", [True, False])
@pytest.mark.parametrize("stage,failure", [
    (False, "native"), (True, "native"),
    (False, "artifact_mutation"), (True, "artifact_mutation"),
    (False, "manifest_mutation"), (True, "manifest_mutation"),
    (True, "copy_manifest_mutation"),
])
def test_refusal_precedes_claim_service_backup_and_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_install: bool,
    stage: bool,
    failure: str,
) -> None:
    current = tmp_path / "current"
    if not first_install:
        old = _app(current / "app", SHA_A, "old", include_cli=False)
        payload = json.loads((old / "manifest.json").read_text())
        payload.pop("runtime_contract")
        (old / "manifest.json").write_text(json.dumps(payload))
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    current.mkdir(exist_ok=True)
    database = current / "database"
    database.write_text("untouched")
    events: list[str] = []
    calls = []

    def refuse(root: Path, manifest: object) -> None:
        calls.append(root)
        if failure == "copy_manifest_mutation":
            return
        if stage and root == candidate:
            return
        if failure == "native":
            raise AppValidationError("native operation failed")
        if failure == "artifact_mutation":
            (root / "new").write_text("probe changed payload")
        else:
            # The manifest is outside the artifact digest. Full identity equality
            # must still reject a probe that substitutes it after initial validation.
            path = root / "manifest.json"
            value = json.loads(path.read_text())
            value["source_digest"] = "f" * 64
            path.write_text(json.dumps(value))

    if failure == "copy_manifest_mutation":
        copytree = shutil.copytree

        def changed_copy(
            source: Path, destination: Path, *args: object, **kwargs: object
        ) -> Path:
            result = copytree(source, destination, *args, **kwargs)  # type: ignore[arg-type]
            path = Path(destination) / "manifest.json"
            if path.is_file():
                value = json.loads(path.read_text())
                value["source_digest"] = "f" * 64
                path.write_text(json.dumps(value))
            return result

        monkeypatch.setattr("planner.environments.deployment.shutil.copytree", changed_copy)
    monkeypatch.setattr("planner.environments.deployment.admit_runtime", refuse)
    with pytest.raises(DeploymentError, match="runtime admission refused|copied candidate differs"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=lambda _: events.append("backup"),  # type: ignore[arg-type, return-value]
            restore=lambda _: events.append("restore"),
            service=FakeService(events),
            health=FakeHealth(events, {SHA_B}),
            before_stop=lambda: events.append("claim"),
        )
    assert not events
    assert len(calls) == (2 if stage and failure != "copy_manifest_mutation" else 1)
    assert database.read_text() == "untouched"
    assert (current / "app/old").exists() == (not first_install)
    assert not (current / "app/new").exists()
    assert not list(current.glob(".app-candidate-*"))


def test_contract_binding_and_legacy_reads(tmp_path: Path) -> None:
    root = _app(tmp_path / "app", SHA_B, "new")
    manifest = validate_app_manifest(root / "manifest.json")
    assert manifest.runtime_contract is not None
    payload = json.loads((root / "manifest.json").read_text())
    payload["runtime_contract"]["python_version"] = "3.12.13"
    (root / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(AppValidationError, match="differs from payload"):
        validate_app_manifest(root / "manifest.json")
    payload.pop("runtime_contract")
    (root / "manifest.json").write_text(json.dumps(payload))
    legacy = validate_app_manifest(root / "manifest.json")
    assert legacy.runtime_contract is None
    with pytest.raises(AppValidationError, match="republish"):
        admission.admit_runtime(root, legacy)
    (root / "runtime/inventory.json").write_text("changed")
    payload["runtime_contract"] = manifest.runtime_contract.as_dict()
    payload["artifact_digest"] = digest_app_artifact(root)
    (root / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(AppValidationError, match="inventory differs"):
        validate_app_manifest(root / "manifest.json")


def test_contract_survives_receipt_and_pinned_request_roundtrip() -> None:
    publication = release()
    decoded = parse_release(json.loads(json.dumps(asdict(publication))))
    assert decoded == publication
    assert decoded.manifest.runtime_contract is not None
    changed = replace(decoded.manifest.runtime_contract, python_version="3.12.13")
    assert (
        replace(decoded, manifest=replace(decoded.manifest, runtime_contract=changed))
        != publication
    )


@pytest.mark.parametrize("failure", ["architecture", "libc", "library", "native"])
def test_host_refusal_precedes_payload_and_probe_failures_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    root = _app(tmp_path / "app", SHA_B, "new")
    manifest = validate_app_manifest(root / "manifest.json")
    commands = []
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        platform, "machine", lambda: "arm64" if failure == "architecture" else "x86_64"
    )
    monkeypatch.setattr(
        platform, "freedesktop_os_release", lambda: {"ID": "debian", "VERSION_ID": "13"}
    )
    monkeypatch.setattr(
        platform, "libc_ver", lambda: ("glibc", "2.1" if failure == "libc" else "2.41")
    )
    assert manifest.runtime_contract is not None
    cpuinfo = "flags: " + " ".join(manifest.runtime_contract.cpu_features)
    read_text = Path.read_text

    def synthetic_cpuinfo(path: Path, *args: object, **kwargs: object) -> str:
        if path == Path("/proc/cpuinfo"):
            return cpuinfo
        return read_text(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", synthetic_cpuinfo)
    monkeypatch.setattr(ctypes, "CDLL", lambda name: None)
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: "/synthetic/tool")

    def run(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        if command[-1] == "--version":
            return "v22.0.0"
        raise AppValidationError("native failure")

    if failure == "library":
        def missing_library(name: str) -> None:
            raise OSError("missing host library")
        monkeypatch.setattr(ctypes, "CDLL", missing_library)
    monkeypatch.setattr(admission, "_run_probe", run)
    with pytest.raises(AppValidationError):
        admission.admit_runtime(root, manifest)
    assert bool(commands) == (failure == "native")


def test_runtime_validation_and_admission_keep_credentials_out_of_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This replaces the original runtime-validation credential regression at the
    # new boundary: validation never executes, admission executes scrubbed probes.
    forbidden = (
        "GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "PLAN_GROQ_API_KEY", "AWS_SECRET_ACCESS_KEY", "UNLISTED_SENTINEL",
        "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "PLAN_DB_PATH",
    )
    for name in forbidden:
        monkeypatch.setenv(name, "ambient-sentinel")
    ambient_home = tmp_path / "operator-home"
    monkeypatch.setenv("HOME", str(ambient_home))
    monkeypatch.setenv("CODEX_HOME", str(ambient_home / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(ambient_home / "claude"))
    root = _app(tmp_path / "app", SHA_B, "new")
    markers = [tmp_path / "python-environment", tmp_path / "tool-environment"]
    executables = [root / ".venv/bin/python", root / "bin/native-tool"]
    for executable, marker in zip(executables, markers, strict=True):
        checks = "".join(f'test -z "${{{name}+x}}" || exit 17\n' for name in forbidden)
        executable.write_text(
            f"#!/bin/sh\n: > {shlex.quote(str(marker))}\n"
            + checks
            + f"/usr/bin/env > {shlex.quote(str(marker))}\n"
        )
        executable.chmod(0o755)
    manifest_path = root / "manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["artifact_digest"] = digest_app_artifact(root)
    manifest_path.write_text(json.dumps(payload))

    manifest = validate_app_manifest(manifest_path, expected_sha=SHA_B, require_runtime=True)
    assert all(not marker.exists() for marker in markers)

    # Keep the platform and package-selection prerequisites synthetic. The public
    # admission entrypoint, candidate processes, and environment boundary are real.
    monkeypatch.setattr(admission, "verify_runtime_host", lambda *args, **kwargs: None)
    monkeypatch.setattr(admission, "native_tool_commands", lambda *args: [[str(executables[1])]])
    admission.admit_runtime(root, manifest)
    environments = [
        dict(line.split("=", 1) for line in marker.read_text().splitlines())
        for marker in markers
    ]
    for environment in environments:
        assert not set(forbidden) & environment.keys()
        assert environment["HOME"] != str(ambient_home)
        assert environment["PWD"] == environment["HOME"] == environment["TMPDIR"]
        assert environment["CODEX_HOME"] == str(Path(environment["HOME"]) / "codex")
        assert environment["CLAUDE_CONFIG_DIR"] == str(Path(environment["HOME"]) / "claude")
        assert not Path(environment["HOME"]).exists()
    assert environments[0]["HOME"] == environments[1]["HOME"]


def test_timeout_kills_owned_probe_group(tmp_path: Path) -> None:
    with pytest.raises(AppValidationError, match="timed out"):
        admission._run_probe(["/bin/sh", "-c", "sleep 30 & wait"], state=tmp_path, timeout=0.05)
