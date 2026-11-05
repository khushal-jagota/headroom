"""Prove old-server claim compatibility and explicit recovery without a live host.

The existing pre-Mac test stops before a successful claim. Reuse deployment fakes
and real archive, request, lifecycle, cutover and rollback code for that missing
boundary. Each case owns tmp_path; no extra browser or connected host is needed.
"""

from __future__ import annotations

import json
import zipfile
from argparse import Namespace
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.unit.test_deployment import (
    SHA_A,
    SHA_B,
    FakeHealth,
    FakeService,
    _app,
    synthetic_runtime_admission,  # noqa: F401
)

from planner.environments import update_runner
from planner.environments.app import validate_app_manifest
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore, lifecycle_path
from planner.environments.update_artifacts import create_app_archive
from planner.environments.update_contracts import OwnerUpdateRequest, PublishedUpdate
from planner.environments.update_reporting import finalize
from planner.environments.update_requests import FileUpdateRequestStore


@dataclass
class Runner:
    arguments: Namespace
    store: FileUpdateRequestStore
    lifecycle: DeploymentLifecycleStore
    events: list[str]
    health: FakeHealth
    status: dict[str, object]
    wire_change: str | None = None


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runner:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    archive = tmp_path / "panels-app.tar.gz"
    checksum = create_app_archive(candidate, archive)
    release = PublishedUpdate(
        1, 2, 3, validate_app_manifest(candidate / "manifest.json"), checksum,
        "video_upload_ledger", (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    store = FileUpdateRequestStore(current / "data" / "owner-update-request.json")
    store.path.parent.mkdir()
    (store.path.parent / "planner.db").write_text("database before install")
    request = OwnerUpdateRequest(
        "request", release, "now", "dispatched", datetime.now(UTC).isoformat(),
        confirm_activity=True,
    )
    payload = asdict(request)
    for key in ("deployment_id", "deadline_at", "dispatched_at"):
        del payload[key]
    del payload["release"]["mac"]
    store.path.write_text(json.dumps(payload))
    arguments = Namespace(
        command="apply", current_root=current, request_id="request", publication_run_id=1,
        artifact_id=2, proof_artifact_id=3, app_sha=SHA_B,
        artifact_digest=release.manifest.artifact_digest, archive_sha256=checksum,
        health_url="http://127.0.0.1:8767/api/health",
    )
    events: list[str] = []
    health = FakeHealth(events, {SHA_A, SHA_B})
    instance = Runner(
        arguments, store, DeploymentLifecycleStore(lifecycle_path(current)), events, health,
        {"state": "unknown", "deployed_sha": SHA_A, "target_sha": SHA_B,
         "installation_confirmed": False, "work_paused": True},
    )

    class Provider:
        def publication(self, run_id: int) -> PublishedUpdate:
            assert run_id == 1
            return release

        def download_artifact(self, artifact_id: int, destination: Path) -> None:
            with zipfile.ZipFile(destination, "w") as zipped:
                if artifact_id == 3:
                    zipped.writestr("publication.json", json.dumps(asdict(release)))
                else:
                    assert artifact_id == 2
                    zipped.write(archive, "panels-app.tar.gz")

    def claim(url: str, **kwargs: Any) -> httpx.Response:
        assert instance.arguments.command == "apply", "recovery must not repeat admission"
        events.append("claim")
        payload = json.loads(store.path.read_text())
        payload["state"] = "applying"
        store.path.write_text(json.dumps(payload))  # the old server's actual wire shape
        if instance.wire_change == "request":
            payload["request_id"] = "other"
        elif instance.wire_change == "release":
            payload["release"]["artifact_id"] = 99
        elif instance.wire_change == "stored_release":
            payload["release"]["artifact_id"] = 99
            store.path.write_text(json.dumps(payload))
        elif instance.wire_change == "state":
            payload["state"] = "dispatched"
        elif instance.wire_change == "attempt":
            payload["deployment_id"] = "different-attempt"
            store.path.write_text(json.dumps(payload))
        return httpx.Response(200, json=payload)

    def get(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/api/health"):
            assert kwargs["params"] == {"expected_sha": SHA_A}
            return httpx.Response(200, json={"ready": SHA_A in health.healthy, "app_sha": SHA_A})
        if url.endswith("/api/deployment-status"):
            return httpx.Response(200, json=instance.status)
        assert url.endswith("/api/updates")
        payload = json.loads(store.path.read_text())
        if instance.wire_change == "request":
            payload["request_id"] = "other"
        elif instance.wire_change == "request_race":
            changed = dict(payload, detail="changed during live proof")
            store.path.write_text(json.dumps(changed))
        return httpx.Response(200, json={"request": payload})

    def backup(*args: Any) -> Path:
        events.append("backup")
        snapshot = tmp_path / "snapshot"
        snapshot.write_text((store.path.parent / "planner.db").read_text())
        return snapshot

    def restore(snapshot: Path, database: Path, **kwargs: Any) -> None:
        events.append("restore")
        database.write_text(snapshot.read_text())

    monkeypatch.setattr(update_runner, "GitHubUpdateProvider", lambda *a, **kw: Provider())
    monkeypatch.setattr(update_runner, "validate_schema_ancestry", lambda *a: None)
    monkeypatch.setattr(
        update_runner, "SubprocessServiceController", lambda *a: FakeService(events)
    )
    monkeypatch.setattr(update_runner, "HttpHealthClient", lambda *a: health)
    monkeypatch.setattr(update_runner, "run_current_app_backup", backup)
    monkeypatch.setattr(update_runner, "restore_database_snapshot", restore)
    monkeypatch.setattr("planner.environments.deployment.capture_after_stop", lambda *a: None)
    monkeypatch.setattr("planner.environments.update_runner.httpx.post", claim)
    monkeypatch.setattr("planner.environments.update_runner.httpx.get", get)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repository")
    monkeypatch.setenv("PANELS_DEPLOYMENT_ID", "attempt")
    return instance


def _accepted_legacy_attempt(runner: Runner) -> None:
    payload = json.loads(runner.store.path.read_text())
    payload["state"] = "applying"
    payload["created_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    runner.store.path.write_text(json.dumps(payload))
    runner.lifecycle.start("failed-attempt", SHA_B, prior_sha=SHA_A)
    runner.lifecycle.transition(
        "failed-attempt", "unknown", code="admission_evidence_uncertain"
    )
    runner.arguments.command = "recover-legacy"
    runner.arguments.failed_deployment_id = "failed-attempt"
    runner.arguments.prior_sha = SHA_A
    runner.arguments.deployment_id = "recovery-attempt"


def test_successful_legacy_claim_uses_canonical_receipt(runner: Runner) -> None:
    result = update_runner.apply(runner.arguments)
    assert result["status"] == "succeeded"
    receipt = runner.store.read()
    assert receipt is not None and receipt.state == "applied"
    assert receipt.deployment_id == "attempt"
    assert runner.events[:3] == ["claim", "stop", "backup"]


@pytest.mark.parametrize("change", ["request", "release", "stored_release", "state", "attempt"])
def test_changed_claim_never_authorizes_service_stop(runner: Runner, change: str) -> None:
    runner.wire_change = change
    with pytest.raises(ValueError, match="admission|applying|attempt"):
        update_runner.apply(runner.arguments)
    assert runner.events == ["claim"]
    assert (runner.arguments.current_root / "app" / "old").exists()


def test_expired_legacy_attempt_recovers_through_existing_transaction(runner: Runner) -> None:
    _accepted_legacy_attempt(runner)
    result = update_runner.apply(runner.arguments)
    assert result["status"] == "succeeded"
    receipt = runner.store.read()
    assert receipt is not None and receipt.state == "applied"
    assert receipt.deployment_id == "recovery-attempt"
    assert receipt.deadline_at is not None
    assert datetime.fromisoformat(receipt.deadline_at) > datetime.now(UTC)
    assert runner.events[:2] == ["stop", "backup"]
    assert "claim" not in runner.events
    assert (runner.arguments.current_root / "app" / "new").exists()
    report = finalize(
        current_root=runner.arguments.current_root, request_id="request",
        deployment_id="recovery-attempt", app_sha=SHA_B, serving_sha=SHA_B,
        apply_outcome="success", provision_outcome="success", published_route_outcome="success",
    )
    assert report["phase"] == "succeeded"
    projection = runner.lifecycle.project(SHA_B, admission_open=True)
    assert projection.installation_confirmed and not projection.work_paused


@pytest.mark.parametrize(
    "change", ["prior", "lifecycle", "modern_request", "request", "request_race",
               "unpaused", "health"]
)
def test_recovery_refuses_changed_evidence_without_writes(runner: Runner, change: str) -> None:
    _accepted_legacy_attempt(runner)
    if change == "prior":
        runner.arguments.prior_sha = "c" * 40
    elif change == "lifecycle":
        runner.arguments.failed_deployment_id = "different-attempt"
    elif change == "modern_request":
        payload = json.loads(runner.store.path.read_text())
        payload["deployment_id"] = "modern-attempt"
        runner.store.path.write_text(json.dumps(payload))
    elif change in {"request", "request_race"}:
        runner.wire_change = change
    elif change == "unpaused":
        runner.status["work_paused"] = False
    elif change == "health":
        runner.health.healthy.remove(SHA_A)
    before = runner.store.path.read_bytes(), runner.lifecycle.path.read_bytes()
    with pytest.raises(ValueError):
        update_runner.apply(runner.arguments)
    assert runner.events == []
    assert runner.lifecycle.path.read_bytes() == before[1]
    if change == "request_race":
        receipt = runner.store.read()
        assert receipt is not None and receipt.detail == "changed during live proof"
    else:
        assert runner.store.path.read_bytes() == before[0]
    assert (runner.arguments.current_root / "app" / "old").exists()


def test_recovery_rolls_back_when_candidate_health_fails(runner: Runner) -> None:
    _accepted_legacy_attempt(runner)
    runner.health.healthy.remove(SHA_B)
    result = update_runner.apply(runner.arguments)
    assert result["status"] == "rolled_back"
    assert (runner.arguments.current_root / "app" / "old").exists()
    assert not (runner.arguments.current_root / "app" / "new").exists()
    assert (runner.store.path.parent / "planner.db").read_text() == "database before install"
    assert "restore" in runner.events
    receipt = runner.store.read()
    assert receipt is not None and receipt.state == "failed"
