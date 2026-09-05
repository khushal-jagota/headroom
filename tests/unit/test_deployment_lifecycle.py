from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from planner.environments.deployment_lifecycle import (
    MAX_LIFECYCLE_BYTES,
    DeploymentLifecycleConflict,
    DeploymentLifecycleError,
    DeploymentLifecycleStore,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
START = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = START

    def __call__(self) -> datetime:
        return self.value


def _store(tmp_path: Path, clock: Clock | None = None) -> DeploymentLifecycleStore:
    return DeploymentLifecycleStore(
        tmp_path / "current/data/deployment-lifecycle.json",
        now=clock or Clock(),
    )


def test_start_transition_read_and_additive_v1_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    started = store.start(
        "deploy-1",
        SHA_B,
        expected_deployment_id=None,
        prior_sha=SHA_A,
    )
    assert started.phase == "preparing"
    assert store.transition("deploy-1", "restarting").phase == "restarting"
    assert store.transition("deploy-1", "verifying").phase == "verifying"
    healthy = store.transition("deploy-1", "app_healthy", serving_sha=SHA_B)
    assert healthy.serving_sha == SHA_B
    assert store.transition("deploy-1", "succeeded").phase == "succeeded"

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["future_addition"] = {"bounded": True}
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    read = store.read()
    assert read.available is True
    assert read.lifecycle is not None
    assert read.lifecycle.phase == "succeeded"


def test_malformed_json_oversize_and_symlink_are_unavailable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True)
    store.path.write_text("{", encoding="utf-8")
    assert store.read().available is False
    store.path.write_bytes(b"x" * (MAX_LIFECYCLE_BYTES + 1))
    assert store.read().reason == "deployment status file is too large"
    store.path.unlink()
    target = tmp_path / "target"
    target.write_text("{}", encoding="utf-8")
    store.path.symlink_to(target)
    assert store.read().available is False


def test_stale_start_and_transition_cannot_overwrite_newer_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.start("deploy-1", SHA_A, expected_deployment_id=None)
    store.start("deploy-2", SHA_B, expected_deployment_id="deploy-1")

    with pytest.raises(DeploymentLifecycleConflict):
        store.start("deploy-stale", SHA_A, expected_deployment_id="deploy-1")
    with pytest.raises(DeploymentLifecycleConflict):
        store.transition("deploy-1", "restarting")

    read = store.read()
    assert read.lifecycle is not None
    assert read.lifecycle.deployment_id == "deploy-2"
    assert read.lifecycle.phase == "preparing"


def test_atomic_replace_fault_leaves_previous_record_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.start("deploy-1", SHA_A, expected_deployment_id=None)
    original = store.path.read_bytes()

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("injected")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(DeploymentLifecycleError, match="persist"):
        store.transition("deploy-1", "restarting")
    assert store.path.read_bytes() == original
    assert list(store.path.parent.glob(f".{store.path.name}.*.tmp")) == []


def test_rollback_requires_proven_prior_serving_sha(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.start("deploy-1", SHA_B, expected_deployment_id=None, prior_sha=SHA_A)
    store.transition("deploy-1", "restarting")
    with pytest.raises(DeploymentLifecycleError, match="prior serving SHA"):
        store.transition("deploy-1", "rolled_back", serving_sha=SHA_B)
    rolled_back = store.transition("deploy-1", "rolled_back", serving_sha=SHA_A)
    assert rolled_back.serving_sha == SHA_A
    assert store.project(SHA_A).outcome == "rolled_back"


