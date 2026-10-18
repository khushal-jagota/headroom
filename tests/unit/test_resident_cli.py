from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.resident.cli import main
from planner.environments.resident.contracts import (
    HostDeploymentResult,
    ResidentDeploymentRecord,
    TwoHostDeploymentResult,
)
from planner.environments.resident.report import write_deployment_record
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION

SHA = "a" * 40


def test_preflight_defers_before_any_deployment(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preflight", "--requested-sha", SHA]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["disposition"] == "deferred"
    assert output["requested_revision"] == SHA
    assert "Explicit executor artifact" in output["detail"]


def test_preflight_rejects_abbreviated_revision() -> None:
    with pytest.raises(SystemExit) as error:
        main(["preflight", "--requested-sha", "abcdef"])
    assert error.value.code == 2


def test_workflow_preflight_needs_only_source_and_standard_library(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "planner.environments.resident.cli",
            "preflight",
            "--requested-sha",
            SHA,
        ],
        cwd=tmp_path,
        env={"PYTHONPATH": str(source)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["disposition"] == "deferred"
    assert not list(tmp_path.iterdir())


def test_report_verification_requires_expected_identity_and_two_hosts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vps = HostDeploymentResult("vps", SHA, "verified", SHA, "Observed exact revision")
    mac = HostDeploymentResult("mac", SHA, "unreachable", None, "Host unavailable")
    path = tmp_path / "report.json"
    record = ResidentDeploymentRecord(1, "run-1", TwoHostDeploymentResult(SHA, vps, mac, False))
    write_deployment_record(path, record)
    arguments = [
        "verify-report",
        "--path",
        str(path),
        "--deployment-id",
        "run-1",
        "--requested-sha",
        SHA,
    ]
    assert main(arguments) == 3
    assert json.loads(capsys.readouterr().out)["result"]["verified"] is False
    assert main([*arguments[:-1], "b" * 40]) == 2
    assert "does not match" in json.loads(capsys.readouterr().out)["error"]
    write_deployment_record(
        path,
        replace(
            record,
            result=replace(
                record.result,
                mac=replace(mac, disposition="verified", observed_revision=SHA),
                verified=True,
            ),
        ),
    )
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["result"]["verified"] is True


def test_source_build_uses_committed_bytes_and_requested_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    module = source / "src" / "example" / "runner.py"
    module.parent.mkdir(parents=True)
    module.write_text("print('committed')\n")
    (source / "requirements.lock").write_text("")
    (source / "requirements-build.txt").write_text("")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        [
            "git",
            "-C",
            str(source),
            "rev-parse",
            "HEAD",
        ],
        text=True,
    ).strip()
    module.write_text("print('dirty source')\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "application_revision": revision,
                "protocol_version": PROTOCOL_VERSION,
                "spool_format_version": SPOOL_FORMAT_VERSION,
                "target_os": "darwin",
                "target_architecture": "arm64",
                "python_version": "3.14.3",
                "closure": {
                    "files": [
                        "src/example/runner.py",
                        "requirements.lock",
                        "requirements-build.txt",
                    ],
                    "entrypoint_module": "example.runner",
                    "python_requirements": "requirements.lock",
                    "node_package_roots": [],
                },
            }
        )
    )
    candidate = tmp_path / "candidate"
    arguments = [
        "source-build",
        "--source-root",
        str(source),
        "--manifest",
        str(manifest),
        "--requested-sha",
        revision,
        "--candidate-app",
        str(candidate),
    ]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["disposition"] == "prepared"
    assert (candidate / "src/example/runner.py").read_text() == "print('committed')\n"
    assert not (candidate / "bin/python").exists()
    other = tmp_path / "other"
    arguments[arguments.index(revision)] = SHA
    arguments[-1] = str(other)
    assert main(arguments) == 2
    assert not other.exists()
