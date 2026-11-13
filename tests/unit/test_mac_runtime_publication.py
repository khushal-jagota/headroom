"""Linux fixture proofs for native publication admission, not native execution.

The hosted arm64 job must still execute the relocated native runtime before it
creates the receipt. These tests prove its inputs and the VPS publication binding.
"""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import build_mac_runtime
from scripts.build_mac_runtime import digest_file, runtime_requirements, write_panels_launcher
from scripts.update_publication import validate_mac_publication

from planner.environments.mac_runtime_configuration import MAC_PYTHON_SHA256, MAC_PYTHON_VERSION


def publication(directory: Path) -> dict[str, Any]:
    archive = directory / "panels-mac-runtime.tar.gz"
    manifest = json.dumps(
        {
            "schema_version": 1,
            "application_revision": "a" * 40,
            "platform": "darwin",
            "architecture": "arm64",
        }
    ).encode()
    with tarfile.open(archive, "w:gz") as packed:
        entry = tarfile.TarInfo("mac-runtime.json")
        entry.size = len(manifest)
        packed.addfile(entry, io.BytesIO(manifest))
    return {
        "schema_version": 1,
        "application_revision": "a" * 40,
        "publication_run_id": 12,
        "archive_sha256": digest_file(archive),
        "archive_size": archive.stat().st_size,
        "python_archive_sha256": MAC_PYTHON_SHA256,
        "python_version": MAC_PYTHON_VERSION,
        "proof": {
            "passed": True,
            "application_revision": "a" * 40,
            "relocation": True,
            "cli_help": True,
            "backend_entrypoints": True,
            "forbidden_dependencies": [],
            "native": [{"path": "python/bin/python3", "architectures": ["arm64"]}],
            "native_operations": {
                "native": "passed",
                "python": MAC_PYTHON_VERSION,
                "platform": "darwin",
                "architecture": "arm64",
                "cli_import": True,
                "mac_service_import": True,
                "sqlite": True,
                "aiohttp": True,
                "cryptography": True,
                "icu": True,
            },
        },
    }


def test_native_receipt_binds_source_run_artifact_and_sealed_bytes(tmp_path: Path) -> None:
    receipt = publication(tmp_path)
    path = tmp_path / "mac-runtime-publication.json"
    path.write_text(json.dumps(receipt))
    admitted = validate_mac_publication(
        tmp_path,
        requested="a" * 40,
        run_id=12,
        artifact_id=34,
        build_result="success",
    )
    assert admitted.artifact_id == 34
    assert admitted.archive_sha256 == receipt["archive_sha256"]
    assert admitted.archive_size == receipt["archive_size"]
    with pytest.raises(ValueError, match="successful native build"):
        validate_mac_publication(
            tmp_path,
            requested="a" * 40,
            run_id=12,
            artifact_id=34,
            build_result="failure",
        )
    for name, replacement in (
        ("application_revision", "b" * 40),
        ("publication_run_id", 13),
        ("archive_size", receipt["archive_size"] + 1),
        ("archive_sha256", "0" * 64),
    ):
        path.write_text(json.dumps({**receipt, name: replacement}))
        with pytest.raises(ValueError, match="differs from the source"):
            validate_mac_publication(
                tmp_path,
                requested="a" * 40,
                run_id=12,
                artifact_id=34,
                build_result="success",
            )
    receipt["proof"]["native_operations"]["icu"] = False
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="native execution proof"):
        validate_mac_publication(
            tmp_path,
            requested="a" * 40,
            run_id=12,
            artifact_id=34,
            build_result="success",
        )


def test_runtime_roots_use_exact_canonical_pins_without_development_tools(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies=["aiohttp", "httpx[http2]", "PyICU==2.16.2"]\n'
    )
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("aiohttp==3.14.3\nhttpx==0.28.1\nPyICU==2.16.2\npytest==9.1.1\n")
    assert runtime_requirements(tmp_path) == [
        "aiohttp==3.14.3",
        "httpx[http2]==0.28.1",
        "PyICU==2.16.2",
    ]
    build_mac_runtime.validate_installed_pins(
        {"aiohttp": "3.14.3", "bootstrap": "1"}, {"aiohttp": "aiohttp==3.14.3"}, {"bootstrap": "1"}
    )
    with pytest.raises(ValueError, match="unpinned dependency"):
        build_mac_runtime.validate_installed_pins({"unplanned-transitive": "1"}, {}, {})
    requirements.write_text("aiohttp==3.14.3\n")
    with pytest.raises(ValueError, match="canonical pin"):
        runtime_requirements(tmp_path)


def test_panels_launcher_uses_its_relocated_interpreter_with_spaces(tmp_path: Path) -> None:
    original = tmp_path / "builder"
    binary = original / "python/bin"
    binary.mkdir(parents=True)
    fake = binary / "python3"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    write_panels_launcher(original)
    relocated = tmp_path / "runtime with spaces"
    original.rename(relocated)
    result = subprocess.check_output(
        [str(relocated / "python/bin/panels"), "ticket", "--help"],
        text=True,
    )
    assert result.splitlines() == [
        "-I",
        "-B",
        "-m",
        "planner.executor.entrypoint",
        "ticket",
        "--help",
    ]
    assert not original.exists()


@pytest.mark.parametrize(
    ("minimum", "dependency", "reason"),
    [
        ("15.0", "/opt/homebrew/lib/libicuuc.dylib", "unresolved/external library"),
        ("27.0", "/usr/lib/libSystem.B.dylib", "requires newer than macOS 15.0"),
    ],
)
def test_library_audit_rejects_a_foreign_library_or_newer_deployment_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minimum: str, dependency: str, reason: str
) -> None:
    binary = tmp_path / "python/bin/python3"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\xcf\xfa\xed\xfe" + b"fixture")

    def fake_run(command: list[str], **kwargs: object) -> str:
        if command[0].endswith("lipo"):
            return "arm64\n"
        if "-L" in command:
            return f"{binary}:\n\t{dependency} (compatibility version 1.0.0)\n"
        if "-l" in command:
            return f"cmd LC_BUILD_VERSION\ncmdsize 32\nplatform 1\nminos {minimum}\nsdk 27.0\n"
        return f"{binary}:\n"

    monkeypatch.setattr(build_mac_runtime, "run", fake_run)
    with pytest.raises(ValueError, match=reason):
        build_mac_runtime.audit_native_dependencies(tmp_path, {})


def test_workflow_requires_same_run_native_artifact_before_publication() -> None:
    workflow = yaml.safe_load(
        (Path(__file__).parents[2] / ".github/workflows/deploy.yml").read_text()
    )
    operation = workflow["on"]["workflow_dispatch"]["inputs"]["operation"]
    assert operation["default"] == "publish"
    assert operation["options"] == ["publish", "adopt", "mac-build"]
    jobs = workflow["jobs"]
    # The isolated build runs even if publication preparation is skipped.
    assert jobs["prepare"]["if"] == (
        "${{ inputs.operation != 'mac-build' "
        "&& (inputs.operation != 'adopt' || inputs.rehearsal) }}"
    )
    assert jobs["suites"]["needs"] == "prepare"
    assert "if" not in jobs["suites"]
    assert jobs["publish"]["if"] == (
        "${{ always() && inputs.operation != 'mac-build' "
        "&& (inputs.operation != 'adopt' || inputs.rehearsal) }}"
    )
    native = jobs["mac-runtime"]
    assert "needs" not in native
    assert native["if"] == "${{ inputs.operation != 'adopt' || inputs.rehearsal }}"
    # This is the documented arm64 label, not the unsupported macos-15-arm64 alias.
    assert native["runs-on"] == "macos-15"
    commands = "\n".join(step.get("run", "") for step in native["steps"])
    assert 'platform.machine() == "arm64"' in commands
    assert '--revision "$PANELS_REQUESTED_SHA" --run-id "$GITHUB_RUN_ID"' in commands
    deploy = workflow["jobs"]["publish"]
    assert "mac-runtime" in deploy["needs"]
    download = next(
        step
        for step in deploy["steps"]
        if step.get("name") == "Download checked native Mac publication"
    )
    assert download["with"]["artifact-ids"] == "${{ needs.mac-runtime.outputs.artifact_id }}"
    receipt = next(
        step
        for step in deploy["steps"]
        if step.get("name") == "Write immutable publication receipt"
    )
    assert (
        receipt["env"]["PANELS_MAC_ARTIFACT_ID"] == "${{ needs.mac-runtime.outputs.artifact_id }}"
    )
    assert receipt["env"]["PANELS_MAC_BUILD_RESULT"] == "${{ needs.mac-runtime.result }}"


def test_private_icu_environment_replaces_host_library_and_compiler_inputs(tmp_path: Path) -> None:
    configured = build_mac_runtime.private_icu_environment(
        tmp_path,
        {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
            "PKG_CONFIG_PATH": "/opt/homebrew/icu",
            "PKG_CONFIG_LIBDIR": "/host/pkgconfig",
            "CFLAGS": "-mmacosx-version-min=27.0",
            "CXXFLAGS": "-mmacosx-version-min=27.0",
            "LDFLAGS": "-L/opt/homebrew/lib",
            "MACOSX_DEPLOYMENT_TARGET": "27.0",
        },
    )
    assert configured["PKG_CONFIG_LIBDIR"] == str(tmp_path / "lib/pkgconfig")
    assert configured["PKG_CONFIG_PATH"] == str(tmp_path / "lib/pkgconfig")
    assert configured["PATH"].split(":")[0] == str(tmp_path / "bin")
    assert configured["MACOSX_DEPLOYMENT_TARGET"] == "15.0"
    for key in ("CFLAGS", "CXXFLAGS", "LDFLAGS"):
        assert "-arch arm64" in configured[key]
        assert "-mmacosx-version-min=15.0" in configured[key]
        assert "27.0" not in configured[key] and "/opt/homebrew" not in configured[key]


@pytest.mark.parametrize(
    "commands",
    [
        "cmd LC_BUILD_VERSION\ncmdsize 32\nplatform MACOS\nminos 15.0\nsdk 27.0\n",
        "cmd LC_VERSION_MIN_MACOSX\ncmdsize 16\nversion 15.0\nsdk 27.0\n",
    ],
)
def test_minimum_macos_metadata_is_distinct_from_the_build_sdk(commands: str) -> None:
    assert build_mac_runtime.minimum_macos_versions(commands) == ["15.0"]
    with pytest.raises(ValueError, match="lacks minimum macOS metadata"):
        build_mac_runtime.minimum_macos_versions("cmd LC_RPATH\ncmdsize 32\n")
