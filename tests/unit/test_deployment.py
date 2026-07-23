from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.environments.deployment import DeploymentError, DeploymentResult, deploy_release

SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _release(root: Path, sha: str, marker: str) -> Path:
    root.mkdir()
    (root / marker).write_text(marker, encoding="utf-8")
    from planner.environments.release import digest_release_source

    (root / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-release-v1",
                "release_sha": sha,
                "source_digest": digest_release_source(root),
            }
        ),
        encoding="utf-8",
    )
    return root


class FakeService:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def restart(self) -> None:
        self.events.append("restart")


class FakeHealth:
    def __init__(self, events: list[str], healthy: set[str]) -> None:
        self.events = events
        self.healthy = healthy

    def wait_for_sha(self, sha: str, *, deadline: float) -> bool:
        self.events.append(f"health:{sha}")
        return sha in self.healthy


def test_deploy_backups_prior_manifest_before_switch_and_records_success(tmp_path: Path) -> None:
    release_root = tmp_path / "releases"
    release_root.mkdir()
    prior = _release(release_root / SHA_A, SHA_A, "old")
    candidate = _release(release_root / SHA_B, SHA_B, "new")
    current = tmp_path / "current"
    current.symlink_to(prior, target_is_directory=True)
    events: list[str] = []

    result = deploy_release(
        candidate=candidate,
        current_pointer=current,
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_B}),
        records_path=tmp_path / "records.jsonl",
        now=lambda: 10.0,
    )

    assert result == DeploymentResult("succeeded", SHA_B, SHA_A, None)
    assert events == [f"backup:{SHA_A}", "restart", f"health:{SHA_B}"]
    assert current.resolve() == candidate
    assert json.loads((tmp_path / "records.jsonl").read_text())[
        "result"
    ] == "succeeded"


def test_failed_candidate_validation_does_not_backup_or_switch(tmp_path: Path) -> None:
    current = tmp_path / "current"
    prior = _release(tmp_path / "prior", SHA_A, "old")
    current.symlink_to(prior, target_is_directory=True)
    events: list[str] = []
    with pytest.raises(DeploymentError, match="candidate"):
        deploy_release(
            candidate=tmp_path / "missing",
            current_pointer=current,
            backup=lambda revision: events.append(f"backup:{revision}"),
            service=FakeService(events),
            health=FakeHealth(events, {SHA_B}),
            records_path=tmp_path / "records.jsonl",
        )
    assert events == []
    assert current.resolve() == prior


def test_failed_candidate_health_rolls_back_code_and_proves_prior(tmp_path: Path) -> None:
    prior = _release(tmp_path / SHA_A, SHA_A, "old")
    candidate = _release(tmp_path / SHA_B, SHA_B, "new")
    current = tmp_path / "current"
    current.symlink_to(prior, target_is_directory=True)
    events: list[str] = []
    health = FakeHealth(events, {SHA_A})
    result = deploy_release(
        candidate=candidate,
        current_pointer=current,
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=health,
        records_path=tmp_path / "records.jsonl",
    )
    assert result.status == "rolled_back"
    assert result.requested_sha == SHA_B
    assert result.prior_sha == SHA_A
    assert current.resolve() == prior
    assert events == [f"backup:{SHA_A}", "restart", f"health:{SHA_B}", "restart", f"health:{SHA_A}"]


def test_same_sha_is_idempotent_without_backup_or_restart(tmp_path: Path) -> None:
    prior = _release(tmp_path / SHA_A, SHA_A, "old")
    current = tmp_path / "current"
    current.symlink_to(prior, target_is_directory=True)
    events: list[str] = []
    result = deploy_release(
        candidate=prior,
        current_pointer=current,
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_A}),
        records_path=tmp_path / "records.jsonl",
    )
    assert result.status == "unchanged"
    assert events == []
