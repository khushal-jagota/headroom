from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from planner.environments.deployment_lifecycle import (
    MAX_LIFECYCLE_BYTES,
    DeploymentLifecycleConflict,
    DeploymentLifecycleError,
    DeploymentLifecycleStore,
    main,
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


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"version": 2}, "malformed"),
        ({"version": True}, "malformed"),
        ({"requested_sha": "not-a-sha"}, "malformed"),
        ({"requested_sha": "a" * 64}, "malformed"),
        ({"phase": "invented"}, "malformed"),
        ({"detail": "x" * 501}, "malformed"),
        ({"updated_at": "2026-07-29T10:00:00"}, "malformed"),
    ],
)
def test_unsupported_or_malformed_core_is_explicitly_unavailable(
    tmp_path: Path,
    change: dict[str, object],
    reason: str,
) -> None:
    store = _store(tmp_path)
    store.start("deploy-1", SHA_B, expected_deployment_id=None)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload.update(change)
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    read = store.read()
    assert read.available is False
    assert read.lifecycle is None
    assert reason in (read.reason or "")


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


def test_freshness_is_bounded_and_problems_persist(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    store.start("deploy-1", SHA_B, expected_deployment_id=None, prior_sha=SHA_A)
    assert store.project(SHA_A).state == "preparing"
    clock.value += timedelta(minutes=16)
    assert store.project(SHA_A).state == "unknown"

    store.start("deploy-2", SHA_B, expected_deployment_id="deploy-1", prior_sha=SHA_A)
    store.transition("deploy-2", "restarting")
    store.transition("deploy-2", "verifying")
    store.transition("deploy-2", "app_healthy", serving_sha=SHA_B)
    store.transition("deploy-2", "succeeded")
    assert store.project(SHA_B).state == "back_up"
    assert store.project(SHA_A).state == "problem"
    clock.value += timedelta(minutes=6)
    settled = store.project(SHA_B)
    assert settled.state == "idle"
    assert settled.outcome == "succeeded"
    assert settled.valid_until is None

    store.start("deploy-3", SHA_B, expected_deployment_id="deploy-2", prior_sha=SHA_A)
    store.transition("deploy-3", "failed", detail="backup failed", code="backup_failed")
    clock.value += timedelta(days=31)
    projection = store.project(SHA_A)
    assert projection.state == "problem"
    assert projection.detail == "backup failed"


def test_rollback_requires_proven_prior_serving_sha(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.start("deploy-1", SHA_B, expected_deployment_id=None, prior_sha=SHA_A)
    store.transition("deploy-1", "restarting")
    with pytest.raises(DeploymentLifecycleError, match="prior serving SHA"):
        store.transition("deploy-1", "rolled_back", serving_sha=SHA_B)
    rolled_back = store.transition("deploy-1", "rolled_back", serving_sha=SHA_A)
    assert rolled_back.serving_sha == SHA_A
    assert store.project(SHA_A).outcome == "rolled_back"


def test_fresh_start_supersedes_old_evidence_and_terminal_preservation_is_a_noop(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.start("deploy-1", SHA_A, expected_deployment_id=None)
    store.transition("deploy-1", "failed")
    store.start("deploy-2", SHA_B, expected_deployment_id="deploy-1")
    store.transition("deploy-2", "failed")
    preserved = store.transition(
        "deploy-2",
        "failed",
        detail="generic finalizer",
        preserve_terminal=True,
    )
    assert preserved.detail is None


def test_missing_and_malformed_records_both_require_an_explicit_empty_expectation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.start("deploy-1", SHA_A, expected_deployment_id=None)
    store.path.write_text("{", encoding="utf-8")

    # Malformed evidence has no readable deployment ID, so a caller that observed
    # that exact state can replace it under the same lock.
    store.start("deploy-2", SHA_B, expected_deployment_id=None)
    lifecycle = store.read().lifecycle
    assert lifecycle is not None
    assert lifecycle.deployment_id == "deploy-2"


def test_stale_empty_expectation_loses_after_another_start(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Runner A observed no readable record. Runner B wins the locked start first.
    store.start("deploy-b", SHA_B, expected_deployment_id=None)
    with pytest.raises(DeploymentLifecycleConflict):
        store.start("deploy-a", SHA_A, expected_deployment_id=None)


def test_stdlib_cli_emits_wire_ready_projection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "deployment-lifecycle.json"
    assert (
        main(
            [
                "start",
                "--path",
                str(path),
                "--deployment-id",
                "deploy-1",
                "--requested-sha",
                SHA_B,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "project",
                "--path",
                str(path),
                "--deployed-sha",
                SHA_A,
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "deployed_sha": SHA_A,
        "detail": None,
        "outcome": None,
        "state": "preparing",
        "target_sha": SHA_B,
        "valid_until": payload["valid_until"],
    }


def test_stdlib_protocol_reads_current_id_for_the_next_locked_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "deployment-lifecycle.json"
    assert main(
        [
            "start",
            "--path",
            str(path),
            "--deployment-id",
            "deploy-1",
            "--requested-sha",
            SHA_A,
        ]
    ) == 0
    capsys.readouterr()
    assert main(["read", "--path", str(path)]) == 0
    observed = json.loads(capsys.readouterr().out)

    assert main(
        [
            "start",
            "--path",
            str(path),
            "--deployment-id",
            "deploy-2",
            "--requested-sha",
            SHA_B,
            "--expected-deployment-id",
            observed["deployment_id"],
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["deployment_id"] == "deploy-2"
