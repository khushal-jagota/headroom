from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from planner.environments import deployment_lifecycle as lifecycle_module
from planner.environments.deployment_configuration import DEPLOYMENT_SUCCESS_DISPLAY_SECONDS
from planner.environments.deployment_lifecycle import (
    DeploymentLifecycleConflict,
    DeploymentLifecycleError,
    DeploymentLifecycleStore,
    DeploymentPhase,
    main,
)

SHA = "a" * 40
PRIOR_SHA = "b" * 40


@dataclass
class Clock:
    value: datetime = datetime(2026, 10, 22, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def test_repeated_renewal_keeps_long_deploy_fresh_then_expires(tmp_path: Path) -> None:
    clock = Clock()
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=clock)
    store.start("deploy", SHA)
    for _ in range(34):
        clock.value += timedelta(minutes=1)
        renewed = store.renew("deploy")
        assert renewed.updated_at == clock.value
        assert renewed.expires_at == clock.value + timedelta(minutes=15)
        assert store.project(PRIOR_SHA).state == "preparing"
    clock.value = renewed.expires_at - timedelta(microseconds=1)
    assert store.project(PRIOR_SHA).state == "preparing"
    clock.value = renewed.expires_at
    assert store.project(PRIOR_SHA).state == "unknown"
    before = store.path.read_bytes()
    with pytest.raises(DeploymentLifecycleConflict, match="expired"):
        store.renew("deploy")
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("phase", ["preparing", "restarting", "verifying", "app_healthy"])
def test_renewal_changes_only_freshness(tmp_path: Path, phase: DeploymentPhase) -> None:
    clock = Clock()
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=clock)
    current = store.start("deploy", SHA, prior_sha=PRIOR_SHA, detail="Preparing", code="ready")
    if phase != "preparing":
        store.transition("deploy", "restarting")
        current = store.transition(
            "deploy", phase, serving_sha=SHA, detail="Still running", code="running"
        )
    clock.value += timedelta(minutes=3)
    renewed = store.renew("deploy")
    assert renewed == replace(
        current, updated_at=clock.value, expires_at=clock.value + timedelta(minutes=15)
    )
    assert store.read().lifecycle == renewed


@pytest.mark.parametrize("unavailable", ["missing", "malformed", "superseded"])
def test_renewal_refuses_unreadable_or_superseded_record(
    tmp_path: Path, unavailable: str
) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=Clock())
    if unavailable == "malformed":
        store.path.write_text('{"version":1,"phase":"preparing"}')
    elif unavailable == "superseded":
        store.start("newer-deploy", SHA)
    before = store.path.read_bytes() if store.path.exists() else None
    with pytest.raises(DeploymentLifecycleConflict):
        store.renew("deploy")
    assert (store.path.read_bytes() if store.path.exists() else None) == before


@pytest.mark.parametrize("phase", ["succeeded", "failed", "rolled_back"])
def test_renewal_preserves_terminal_bytes_even_after_expiry(
    tmp_path: Path, phase: DeploymentPhase
) -> None:
    clock = Clock()
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=clock)
    store.start("deploy", SHA, prior_sha=PRIOR_SHA)
    store.transition("deploy", "restarting")
    store.transition("deploy", "app_healthy", serving_sha=SHA)
    terminal = store.transition(
        "deploy", phase, serving_sha=PRIOR_SHA if phase == "rolled_back" else SHA
    )
    # Noncanonical whitespace detects even a rewrite of identical parsed values.
    store.path.write_text(json.dumps(terminal.to_public_dict(), indent=3) + "\n\n")
    before = store.path.read_bytes()
    clock.value = terminal.expires_at + timedelta(days=1)
    assert store.renew("deploy") == terminal
    assert store.path.read_bytes() == before
    with pytest.raises(DeploymentLifecycleConflict, match="stale"):
        store.renew("old-deploy")
    assert store.path.read_bytes() == before


def test_first_release_derives_brief_success_from_record_and_keeps_day_of_evidence(
    tmp_path: Path,
) -> None:
    clock = Clock()
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=clock)
    store.start("deploy", SHA)
    store.transition("deploy", "restarting")
    with pytest.raises(DeploymentLifecycleError, match="requested serving SHA"):
        store.transition("deploy", "app_healthy", serving_sha=PRIOR_SHA)
    store.transition("deploy", "app_healthy", serving_sha=SHA)
    succeeded = store.transition("deploy", "succeeded")
    assert succeeded.expires_at == clock.value + timedelta(hours=24)
    # The first fresh record and the stale pre-change record share this format:
    # no display field is persisted, so there is no migration default to guess.
    assert "success_display_until" not in json.loads(store.path.read_text())
    before = store.path.read_bytes()
    deadline = succeeded.updated_at + timedelta(
        seconds=DEPLOYMENT_SUCCESS_DISPLAY_SECONDS
    )
    clock.value = deadline - timedelta(microseconds=1)
    projection = store.project(SHA)
    assert projection.state == "back_up"
    assert projection.success_display_until == deadline.isoformat().replace("+00:00", "Z")
    assert projection.valid_until == succeeded.expires_at.isoformat().replace("+00:00", "Z")
    mismatch = store.project(PRIOR_SHA)
    assert mismatch.state == "problem"
    assert mismatch.success_display_until is None
    assert store.renew("deploy") == succeeded
    assert store.project(SHA) == projection
    assert store.path.read_bytes() == before
    clock.value = deadline
    assert store.project(SHA) == projection
    # A stale legacy record remains available to interruption accounting after clearance.
    clock.value = succeeded.updated_at + timedelta(hours=12)
    assert store.project(SHA) == projection
    assert store.read().lifecycle == succeeded
    assert store.path.read_bytes() == before
    clock.value = succeeded.expires_at - timedelta(microseconds=1)
    assert store.project(SHA).state == "back_up"
    clock.value = succeeded.expires_at
    assert store.project(SHA).state == "idle"
    # Shorter record validity remains an independent upper bound on presentation.
    shortened = replace(succeeded, expires_at=succeeded.updated_at + timedelta(seconds=1))
    store.path.write_text(json.dumps(shortened.to_public_dict()))
    clock.value = shortened.expires_at
    assert store.project(SHA).state == "idle"


@pytest.mark.parametrize("renew_first", [True, False])
@pytest.mark.parametrize("phase", ["verifying", "failed"])
def test_serialized_renewal_and_transition_preserve_latest_phase(
    tmp_path: Path, renew_first: bool, phase: DeploymentPhase
) -> None:
    clock = Clock()
    path = tmp_path / "lifecycle.json"
    transition_store = DeploymentLifecycleStore(path, now=clock)
    renewal_store = DeploymentLifecycleStore(path, now=clock)
    transition_store.start("deploy", SHA)
    if renew_first:
        renewal_store.renew("deploy")
    transitioned = transition_store.transition("deploy", phase, detail="Latest progress")
    if not renew_first:
        clock.value += timedelta(minutes=1)
        renewal_store.renew("deploy")
    latest = transition_store.read().lifecycle
    assert latest is not None
    assert latest.phase == phase
    assert latest.detail == "Latest progress"
    if phase == "failed":
        assert latest == transitioned


def test_renewal_lock_timeout_releases_descriptor_and_allows_next_renewal(tmp_path: Path) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=Clock())
    store.start("deploy", SHA)
    lock = lifecycle_module._exclusive_lock(store.lock_path, timeout_seconds=0.01)
    with store.lock_path.open("r+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(DeploymentLifecycleError, match="timed out"):
            with lock:
                pytest.fail("contended lock must not be acquired")
        assert lock.stream is not None and lock.stream.closed
    assert store.renew("deploy").deployment_id == "deploy"


def test_interrupted_lock_acquisition_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=Clock())
    store.start("deploy", SHA)
    lock = lifecycle_module._exclusive_lock(store.lock_path, timeout_seconds=5)

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("planner.environments.deployment_lifecycle.time.sleep", interrupt)
    with store.lock_path.open("r+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        with pytest.raises(KeyboardInterrupt):
            with lock:
                pytest.fail("contended lock must not be acquired")
        assert lock.stream is not None and lock.stream.closed
    assert store.renew("deploy").deployment_id == "deploy"


def test_renew_cli_outputs_public_lifecycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "lifecycle.json"
    DeploymentLifecycleStore(path).start("deploy", SHA)
    assert main(["renew", "--path", str(path), "--deployment-id", "deploy"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == json.loads(path.read_text())
    assert payload["version"] == 1
    assert payload["phase"] == "preparing"


def test_borrowed_lock_survives_renewal_and_readonly_receipt_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=Clock())
    original = store.start("deploy", SHA)
    with lifecycle_module.renewal_lock(store.path) as owner:
        baseline = lifecycle_module.open_renewal_baseline(store.path)
        try:
            assert lifecycle_module.recover_renewal_receipt(
                store.path, "deploy", lock_fd=owner, baseline_fd=baseline,
            ) == {"committed": False}
            # The clock stays equal. Only the pinned inode proves the new commit.
            assert store.renew("deploy", lock_fd=owner) == original
            with store.lock_path.open("r+") as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = store.path.read_bytes()
            identity = store.path.stat().st_ino

            def forbidden_write(_path: Path, _payload: dict[str, object]) -> None:
                pytest.fail("receipt recovery must never write a renewal")

            monkeypatch.setattr(lifecycle_module, "_atomic_write", forbidden_write)
            assert lifecycle_module.recover_renewal_receipt(
                store.path, "deploy", lock_fd=owner, baseline_fd=baseline,
            ) == {"committed": True, "lifecycle": original.to_public_dict()}
            assert store.path.read_bytes() == before
            assert store.path.stat().st_ino == identity
            with store.lock_path.open("r+") as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(baseline)
    with store.lock_path.open("r+") as contender:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("wrong_descriptor", ["other-file", "contended-open-description"])
def test_borrowed_lock_rejects_wrong_identity_or_contended_description(
    tmp_path: Path, wrong_descriptor: str,
) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=Clock())
    store.start("deploy", SHA)
    before = store.path.read_bytes()
    with lifecycle_module.renewal_lock(store.path):
        path = store.path if wrong_descriptor == "other-file" else store.lock_path
        with path.open("r+") as foreign:
            with pytest.raises((DeploymentLifecycleError, BlockingIOError)):
                store.renew("deploy", lock_fd=foreign.fileno())
            # Rejection closes only the borrowed copy, never the caller's descriptor.
            assert os.fstat(foreign.fileno()).st_ino == path.stat().st_ino
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("replacement", ["transition", "other-deployment", "malformed"])
def test_receipt_recovery_rejects_unattributable_replacement(
    tmp_path: Path, replacement: str,
) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=Clock())
    original = store.start("deploy", SHA)
    with lifecycle_module.renewal_lock(store.path) as owner:
        baseline = lifecycle_module.open_renewal_baseline(store.path)
        try:
            payload = original.to_public_dict()
            if replacement == "transition":
                payload["phase"] = "failed"
            elif replacement == "other-deployment":
                payload["deployment_id"] = "someone-else"
            else:
                payload = {"version": 1}
            # Simulate evidence that cannot belong to this attempt, despite a new inode.
            lifecycle_module._atomic_write(store.path, payload)
            before = store.path.read_bytes()
            with pytest.raises(DeploymentLifecycleError):
                lifecycle_module.recover_renewal_receipt(
                    store.path, "deploy", lock_fd=owner, baseline_fd=baseline,
                )
            assert store.path.read_bytes() == before
        finally:
            os.close(baseline)


def test_neutral_cancellation_and_uncertainty_have_distinct_projections(tmp_path: Path) -> None:
    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json", now=Clock())
    store.start("cancelled-attempt", SHA, prior_sha=PRIOR_SHA)
    store.transition("cancelled-attempt", "cancelled", detail="Canceled before installation.")
    neutral = store.project(PRIOR_SHA)
    assert (neutral.state, neutral.outcome, neutral.deployed_sha) == (
        "idle", "cancelled", PRIOR_SHA
    )
    before = store.path.read_bytes()
    store.transition("cancelled-attempt", "failed", preserve_terminal=True)
    assert store.path.read_bytes() == before
    store.start("uncertain-attempt", SHA, expected_deployment_id="cancelled-attempt")
    store.transition("uncertain-attempt", "unknown", detail="Admission is uncertain.")
    uncertain = store.project(PRIOR_SHA)
    assert (uncertain.state, uncertain.outcome) == ("unknown", None)
    assert uncertain.detail == "Admission is uncertain."
