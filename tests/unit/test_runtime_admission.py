"""Admission risks absent from the synthetic deployment transaction fixtures."""

from __future__ import annotations

import ctypes
import json
import platform
import shlex
import shutil
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from tests.support.update_apps import SHA_B, release
from tests.support.update_apps import build_app as _app

from planner.environments import runtime_admission as admission
from planner.environments.app import (
    NODE_RUNTIME_FILES,
    AppValidationError,
    digest_app_artifact,
    validate_app_manifest,
)
from planner.environments.runtime_contracts import (
    NodeRuntimeContract,
    RuntimeContract,
    RuntimeContractError,
    parse_optional_runtime_contract,
    runtime_bytes_digest,
)
from planner.environments.update_attempts import parse_release


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
    assert isinstance(decoded.manifest.runtime_contract, RuntimeContract)
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


def test_version_2_contract_is_closed_and_version_1_is_unchanged() -> None:
    """Risk: a near-miss Node contract, or a Python field on it, is admitted."""
    node = NodeRuntimeContract("f" * 64)
    assert parse_optional_runtime_contract(node.as_dict()) == node
    python = RuntimeContract("3.12.12", "f" * 64)
    assert parse_optional_runtime_contract(python.as_dict()) == python
    refused = [
        node.as_dict() | {"python_version": "3.12.12"},
        {key: value for key, value in node.as_dict().items() if key != "minimum_node_version"},
        node.as_dict() | {"minimum_node_version": "22.17.0"},
        node.as_dict() | {"external_executables": ["sh", "node", "python3"]},
        node.as_dict() | {"version": 3},
        node.as_dict() | {"version": True},
        python.as_dict() | {"version": 2},
    ]
    for value in refused:
        with pytest.raises(RuntimeContractError):
            parse_optional_runtime_contract(value)


def node_app(root: Path) -> Path:
    """The shared app tree, re-sealed as a version 2 Node release with no private Python."""
    _app(root, SHA_B, "new")
    shutil.rmtree(root / ".venv")
    for name in NODE_RUNTIME_FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("{}")
    (root / "server/package.json").write_text(json.dumps({"dependencies": DEPENDENCIES}))
    inventory = b'{"node": []}'
    contract = NodeRuntimeContract(runtime_bytes_digest(inventory))
    (root / "runtime/inventory.json").write_bytes(inventory)
    (root / "runtime/contract.json").write_text(json.dumps(contract.as_dict()))
    payload = json.loads((root / "manifest.json").read_text())
    payload["runtime_contract"] = contract.as_dict()
    payload["artifact_digest"] = digest_app_artifact(root)
    (root / "manifest.json").write_text(json.dumps(payload))
    return root


DEPENDENCIES = {"better-sqlite3": "1", "koffi": "1", "@anthropic-ai/claude-agent-sdk": "1"}


def node_report(root: Path, **changes: object) -> dict[str, object]:
    report: dict[str, object] = {
        "node_version": "22.18.0",
        "native": "passed",
        "loaded_libraries": ["/usr/lib/x86_64-linux-gnu/libc.so.6"],
        "package_paths": {
            name: str(root.resolve() / "server/node_modules" / name / "index.js")
            for name in DEPENDENCIES
        },
    }
    return report | changes


def test_version_2_admission_runs_the_trusted_probe_and_never_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: a Node release needs a .venv it lacks, runs a candidate entrypoint, or
    runs its JavaScript tools on a Node other than the resolved host Node."""
    root = node_app(tmp_path / "app")
    manifest = validate_app_manifest(root / "manifest.json", require_runtime=True)
    assert isinstance(manifest.runtime_contract, NodeRuntimeContract)
    monkeypatch.setattr(admission, "verify_runtime_host", lambda *args, **kwargs: None)
    monkeypatch.setattr(admission, "_host_node", lambda: "/host/node")
    probes: list[list[str]] = []
    commands: list[list[str]] = []

    def json_probe(command: list[str], **kwargs: object) -> object:
        probes.append(command)
        return node_report(root)

    monkeypatch.setattr(admission, "_run_json_probe", json_probe)
    def tool_probe(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return ""

    monkeypatch.setattr(admission, "_run_probe", tool_probe)
    admission.admit_runtime(root, manifest)
    [probe] = probes
    assert probe[:3] == ["/host/node", "--input-type=module", "-e"]
    assert probe[3] == admission.node_probe_source(manifest.runtime_contract)
    assert probe[4:] == [str(root.resolve())]
    assert commands[0][0] == commands[1][0] == "/host/node"
    probed = " ".join(" ".join(command) for command in commands)
    assert "server/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude" in probed
    assert ".venv" not in probed


@pytest.mark.parametrize(
    "change",
    [
        {"extra": True},
        {"native": "failed"},
        {"node_version": "22.17.9"},
        {"node_version": "24.1.0"},
        {"loaded_libraries": ["libc.so.6"]},
        {"package_paths": {name: "/usr/lib/node_modules/x/index.js" for name in DEPENDENCIES}},
        {"package_paths": {"better-sqlite3": "/app/x.js"}},
    ],
)
def test_version_2_refuses_a_probe_report_that_is_not_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    """Risk: a malformed, failed, old-Node or outside-package report is accepted."""
    root = node_app(tmp_path / "app")
    contract = NodeRuntimeContract("f" * 64)
    monkeypatch.setattr(
        admission, "_run_json_probe", lambda command, **kwargs: node_report(root, **change)
    )
    with pytest.raises(AppValidationError):
        admission._run_node_probe(
            root, contract, node="/host/node", state=tmp_path, deadline=time.monotonic() + 60
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="host Node is not installed")
def test_trusted_probe_refuses_a_package_that_resolves_outside_the_candidate(
    tmp_path: Path,
) -> None:
    """Risk: a candidate dependency symlinked to a host install passes as candidate code.
    This runs the real probe source on host Node; it fails before any native import."""
    root = tmp_path / "app"
    outside = tmp_path / "host-install/leak"
    outside.mkdir(parents=True)
    (outside / "package.json").write_text('{"name": "leak", "main": "index.js"}')
    (outside / "index.js").write_text("module.exports = 1;\n")
    (root / "server/node_modules").mkdir(parents=True)
    (root / "server/node_modules/leak").symlink_to(outside)
    (root / "server/package.json").write_text('{"dependencies": {"leak": "1"}}')
    state = tmp_path / "state"
    state.mkdir()
    node = str(Path(str(shutil.which("node"))).resolve())
    with pytest.raises(AppValidationError, match="resolves outside the candidate"):
        admission._run_json_probe(
            [node, "--input-type=module", "-e",
             admission.node_probe_source(NodeRuntimeContract("f" * 64)), str(root)],
            state=state, timeout=30,
        )
