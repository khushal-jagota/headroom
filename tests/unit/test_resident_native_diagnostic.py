"""Safe failure facts cross command, remote-result and durable-report boundaries."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from planner.environments.resident import cli, driver, transport
from planner.environments.resident.contracts import (
    HostDeploymentResult,
    NativeBuildDiagnostic,
    NativeBuildFailure,
    ResidentDeploymentRecord,
    ResidentError,
    TwoHostDeploymentResult,
)
from planner.environments.resident.logic.native_diagnostic import (
    format_native_diagnostic,
    parse_native_diagnostic,
)
from planner.environments.resident.report import (
    parse_deployment_record,
    read_deployment_record,
    write_deployment_record,
)

SHA = "a" * 40
DIAGNOSTIC = NativeBuildDiagnostic("backend_npm_install", "npm_ci", "nonzero_exit", 1)
SECRET = "TOKEN-sentinel https://secret-registry.invalid/private"


def _host(detail: str) -> HostDeploymentResult:
    return HostDeploymentResult("mac", SHA, "failed", None, detail)


def _record(host: HostDeploymentResult) -> ResidentDeploymentRecord:
    vps = HostDeploymentResult("vps", SHA, "verified", SHA, "Ready")
    return ResidentDeploymentRecord(1, "run-1", TwoHostDeploymentResult(SHA, vps, host, False))


def test_diagnostic_round_trip_and_legacy_compatibility(tmp_path: Path) -> None:
    detail = format_native_diagnostic(DIAGNOSTIC)
    assert detail == (
        'native-build-diagnostic:{"exit_status":1,"failure_kind":"nonzero_exit",'
        '"operation":"npm_ci","phase":"backend_npm_install"}'
    )
    assert parse_native_diagnostic(detail) == DIAGNOSTIC
    assert parse_native_diagnostic("Native command refused") is None
    host = _host(detail)
    assert transport.parse_host_result(json.dumps(asdict(host)), SHA) == host
    record = _record(host)
    path = tmp_path / "report.json"
    write_deployment_record(path, record)
    assert read_deployment_record(path) == record
    assert set(json.loads(path.read_bytes())) == {"version", "deployment_id", "result"}
    assert json.loads(path.read_bytes())["version"] == 1
    assert SECRET not in path.read_text()


@pytest.mark.parametrize(
    "change",
    [
        {"phase": "backend_npm_install", "operation": "create_venv"},
        {"operation": SECRET},
        {"phase": "unknown"},
        {"failure_kind": "unknown"},
        {"exit_status": True},
        {"exit_status": 0},
        {"exit_status": 256},
        {"exit_status": -256},
        {"exit_status": 1.0},
        {"exit_status": None},
        {"failure_kind": "timeout", "exit_status": 1},
        {"extra": SECRET},
    ],
)
def test_diagnostic_rejects_invalid_values_at_all_boundaries(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    payload = {**asdict(DIAGNOSTIC), **change}
    detail = "native-build-diagnostic:" + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    for parse in (
        lambda: parse_native_diagnostic(detail),
        lambda: transport.parse_host_result(json.dumps(asdict(_host(detail))), SHA),
        lambda: parse_deployment_record(asdict(_record(_host(detail)))),
        lambda: write_deployment_record(tmp_path / "report", _record(_host(detail))),
    ):
        with pytest.raises(ResidentError):
            parse()
    assert not (tmp_path / "report").exists()


@pytest.mark.parametrize(
    "detail",
    [
        "native-build-diagnostic",
        "native-build-diagnostic:{",
        "native-build-diagnostic:[]",
        "\n" + format_native_diagnostic(DIAGNOSTIC),
        "native-build-diagnostic:" + " " * 600,
        'native-build-diagnostic:{"phase":"python_probe"}',
        'native-build-diagnostic:{"phase":"python_probe","phase":"node_probe"}',
    ],
)
def test_reserved_malformed_details_are_refused_before_report_summarization(
    tmp_path: Path,
    detail: str,
) -> None:
    with pytest.raises(ResidentError):
        write_deployment_record(tmp_path / "report", _record(_host(detail)))
    assert not (tmp_path / "report").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"host": "vps"},
        {"disposition": "verified", "observed_revision": SHA},
        {"disposition": "deferred"},
        {"disposition": "unreachable"},
        {"observed_revision": SHA},
    ],
)
def test_diagnostics_require_failed_mac_without_observed_revision(change: dict[str, Any]) -> None:
    host = replace(_host(format_native_diagnostic(DIAGNOSTIC)), **change)
    with pytest.raises(ResidentError):
        parse_deployment_record(asdict(_record(host)))
    with pytest.raises(ResidentError):
        transport.parse_host_result(json.dumps(asdict(host)), SHA)


@pytest.mark.parametrize("status", [-255, -9, 1, 255])
def test_bounded_nonzero_process_status(status: int) -> None:
    diagnostic = replace(DIAGNOSTIC, exit_status=status)
    assert parse_native_diagnostic(format_native_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize("kind", ["timeout", "process_start", "output_limit", "cleanup_deadline"])
def test_failures_without_exit_status(kind: Any) -> None:
    diagnostic = replace(DIAGNOSTIC, failure_kind=kind, exit_status=None)
    assert parse_native_diagnostic(format_native_diagnostic(diagnostic)) == diagnostic


def test_remote_json_is_bounded_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object) -> object:
        pytest.fail("oversized host JSON must be rejected before parsing")

    monkeypatch.setattr(json, "loads", forbidden)
    with pytest.raises(ResidentError, match="too large"):
        transport.parse_host_result(" " * (16 * 1024 + 1), SHA)


@pytest.mark.parametrize("failure", [NativeBuildFailure(DIAGNOSTIC), RuntimeError(SECRET)])
@pytest.mark.parametrize("command", ["deploy-local", "deploy-prepared", "activate"])
def test_native_cli_failure_is_safe_five_field_host_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    command: str,
    tmp_path: Path,
) -> None:
    def fail(*args: object, **kwargs: object) -> Any:
        raise failure

    for name in ("activate", "deploy_local_source", "prepare_and_activate"):
        monkeypatch.setattr(driver, name, fail)
    args = [
        command,
        "--candidate-app",
        "/candidate",
        "--requested-sha",
        SHA,
        "--executor-ssh-host",
        "vps.example",
        "--executor-ssh-port",
        "2222",
        "--executor-ssh-auth-mode",
        "public_key",
        "--executor-ssh-user",
        "owner",
        "--executor-ssh-identity-file",
        "/key",
        "--executor-ssh-known-hosts",
        "/hosts",
        "--panels-server-url",
        "https://panels.example",
    ]
    if command == "deploy-local":
        args += ["--source-root", "/source", "--manifest", "/manifest"]
    assert cli.main(args) == 3
    output = capsys.readouterr().out
    assert SECRET not in output
    host = transport.parse_host_result(output, SHA)
    assert host == _host(
        format_native_diagnostic(DIAGNOSTIC)
        if isinstance(failure, NativeBuildFailure)
        else "Native deployment did not complete"
    )
    path = tmp_path / "report.json"
    write_deployment_record(path, _record(host))
    assert SECRET not in path.read_text()
    assert read_deployment_record(path).result.mac == host
