from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from planner.environments.resident.contracts import (
    HostDeploymentResult,
    ResidentDeploymentRecord,
    ResidentError,
    TwoHostDeploymentResult,
)
from planner.environments.resident.deployment import deploy_two_hosts
from planner.environments.resident.report import (
    parse_deployment_record,
    read_deployment_record,
    write_deployment_record,
)

SHA = "a" * 40


def record(*, verified: bool = True) -> ResidentDeploymentRecord:
    vps = HostDeploymentResult("vps", SHA, "verified", SHA, "Exact revision observed")
    mac = HostDeploymentResult(
        "mac",
        SHA,
        "verified" if verified else "unreachable",
        SHA if verified else None,
        "Exact revision observed" if verified else "Host unavailable",
    )
    return ResidentDeploymentRecord(1, "run-1", TwoHostDeploymentResult(SHA, vps, mac, verified))


def test_report_preserves_partial_host_evidence(tmp_path: Path) -> None:
    path = tmp_path / "reports" / "run-1.json"
    value = record(verified=False)
    write_deployment_record(path, value)
    assert read_deployment_record(path) == value
    assert json.loads(path.read_text())["result"]["mac"]["observed_revision"] is None
    assert {entry.name for entry in path.parent.iterdir()} == {path.name, path.name + ".lock"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_revision", "b" * 40),
        ("host", "vps"),
        ("requested_revision", "b" * 40),
        ("disposition", ["verified"]),
    ],
)
def test_report_rejects_forged_host_evidence(field: str, value: object) -> None:
    payload = asdict(record())
    payload["result"]["mac"][field] = value
    with pytest.raises(ResidentError):
        parse_deployment_record(payload)


def test_report_does_not_trust_aggregate_success() -> None:
    payload = asdict(record(verified=False))
    payload["result"]["verified"] = True
    with pytest.raises(ResidentError, match="aggregate"):
        parse_deployment_record(payload)


def test_report_refuses_other_deployment_identity(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    original = record()
    write_deployment_record(path, original)
    with pytest.raises(ResidentError, match="another deployment"):
        write_deployment_record(path, replace(original, deployment_id="run-2"))
    assert read_deployment_record(path) == original


def test_report_refuses_symlink_parent(tmp_path: Path) -> None:
    directory = tmp_path / "real"
    directory.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ResidentError, match="symlinks"):
        write_deployment_record(link / "report.json", record())
    assert not list(directory.iterdir())


def test_report_publication_failure_preserves_prior_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.json"
    original = record(verified=False)
    write_deployment_record(path, original)

    def fail_replace(*args: object) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        write_deployment_record(path, record())
    assert read_deployment_record(path) == original
    assert {entry.name for entry in tmp_path.iterdir()} == {path.name, path.name + ".lock"}


def test_concurrent_writers_do_not_replace_another_deployment(tmp_path: Path) -> None:
    path = tmp_path / "report.json"

    def write(deployment_id: str) -> bool:
        try:
            write_deployment_record(path, replace(record(), deployment_id=deployment_id))
        except ResidentError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ["run-1", "run-2"]))
    assert sorted(outcomes) == [False, True]
    assert read_deployment_record(path).deployment_id in {"run-1", "run-2"}


def test_coordinator_records_real_failure_details_without_losing_identity(tmp_path: Path) -> None:
    path = tmp_path / "report.json"

    class FailedVps:
        def deploy(self, requested_revision: str) -> HostDeploymentResult:
            raise RuntimeError("Restart failed:\n\t" + "native stderr\x00\x7f" * 100)

    class UpdatedMac:
        def deploy(self, requested_revision: str) -> HostDeploymentResult:
            return HostDeploymentResult(
                "mac", requested_revision, "verified", requested_revision, ""
            )

    result = deploy_two_hosts(
        SHA,
        vps=FailedVps(),
        mac=UpdatedMac(),
        record=lambda result: write_deployment_record(
            path, ResidentDeploymentRecord(1, "run-real-failure", result)
        ),
    )
    saved = read_deployment_record(path)
    assert saved.deployment_id == "run-real-failure"
    assert saved.result.requested_revision == SHA
    assert saved.result.verified is False
    assert result.verified is False
    assert saved.result.vps.disposition == "failed"
    assert saved.result.vps.observed_revision is None
    assert saved.result.vps.detail.startswith("Restart failed: native stderr")
    assert len(saved.result.vps.detail) == 500
    assert saved.result.vps.detail.isprintable()
    assert saved.result.mac.disposition == "verified"
    assert saved.result.mac.observed_revision == SHA
    assert saved.result.mac.detail == "No detail supplied"


def test_parser_still_refuses_unsanitized_details() -> None:
    payload = asdict(record())
    payload["result"]["vps"]["detail"] = "raw\nexception"
    with pytest.raises(ResidentError, match="bounded printable"):
        parse_deployment_record(payload)


@pytest.mark.parametrize("content", [b"{", b"x" * (16 * 1024 + 1)])
def test_report_rejects_invalid_storage(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "report.json"
    path.write_bytes(content)
    with pytest.raises(ResidentError):
        read_deployment_record(path)
