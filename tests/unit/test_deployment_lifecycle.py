from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from planner.environments.deployment_configuration import DEPLOYMENT_SUCCESS_DISPLAY_SECONDS
from planner.environments.deployment_lifecycle import (
    DeploymentLifecycleConflict,
    DeploymentLifecycleError,
    DeploymentLifecycleStore,
    main,
)

SHA = "a" * 40
PRIOR_SHA = "b" * 40


@dataclass
class Clock:
    value: datetime = datetime(2026, 10, 22, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def test_last_step_persists_without_renewal_or_expiry(tmp_path: Path) -> None:
    clock = Clock()
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=clock)
    store.start("deploy", SHA, prior_sha=PRIOR_SHA)
    clock.value += timedelta(minutes=40)
    assert store.project(PRIOR_SHA).state == "preparing"
    store.transition("deploy", "restarting")
    clock.value += timedelta(days=2)
    assert store.read().lifecycle.phase == "restarting"
    assert store.project(PRIOR_SHA).state == "restarting"
    assert "expires_at" not in json.loads(store.path.read_text())
    assert not store.path.with_suffix(".json.lock").exists()


def test_health_proof_and_fixed_success_presentation(tmp_path: Path) -> None:
    clock = Clock()
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=clock)
    store.start("deploy", SHA)
    store.transition("deploy", "verifying")
    with pytest.raises(DeploymentLifecycleError, match="serving SHA"):
        store.transition("deploy", "app_healthy", serving_sha=PRIOR_SHA)
    store.transition("deploy", "app_healthy", serving_sha=SHA)
    completed = store.transition("deploy", "succeeded", serving_sha=SHA)
    assert store.project(SHA, admission_open=True).state == "back_up"
    assert store.project(SHA, admission_open=True).installation_confirmed
    clock.value += timedelta(seconds=DEPLOYMENT_SUCCESS_DISPLAY_SECONDS)
    assert store.project(SHA).state == "idle"
    assert store.read().lifecycle == completed
    assert store.project(PRIOR_SHA).state == "problem"


def test_prior_app_proof_for_rollback_and_stale_writer(tmp_path: Path) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json")
    store.start("deploy", SHA, prior_sha=PRIOR_SHA)
    store.transition("deploy", "restarting")
    with pytest.raises(DeploymentLifecycleError, match="prior serving SHA"):
        store.transition("deploy", "rolled_back", serving_sha=SHA)
    store.transition("deploy", "rolled_back", serving_sha=PRIOR_SHA)
    assert store.project(PRIOR_SHA).outcome == "rolled_back"
    store.start("next", SHA, expected_deployment_id="deploy")
    with pytest.raises(DeploymentLifecycleConflict, match="stale"):
        store.transition("deploy", "failed")


def test_cancellation_is_neutral_and_terminal_finalizer_preserves_result(tmp_path: Path) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json")
    store.start("deploy", SHA)
    store.transition("deploy", "cancelled")
    before = store.path.read_bytes()
    store.transition("deploy", "failed", preserve_terminal=True)
    assert store.path.read_bytes() == before
    assert store.project(PRIOR_SHA).state == "idle"
    assert store.project(PRIOR_SHA).outcome == "cancelled"


def test_missing_and_malformed_status_do_not_claim_success(tmp_path: Path) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json")
    assert store.project(SHA).state == "idle"
    store.path.write_text("broken")
    assert not store.read().available
    assert store.project(SHA).state == "unknown"


def test_cli_writes_each_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = str(tmp_path / "status.json")
    assert main(["start", "--path", path, "--deployment-id", "deploy", "--requested-sha", SHA]) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "preparing"
    assert main([
        "transition", "--path", path, "--deployment-id", "deploy", "--phase", "verifying"
    ]) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "verifying"
    assert main(["read", "--path", path]) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "verifying"
