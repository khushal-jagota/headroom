"""Deadline authority, recovery admission, and the single update answer.

Existing owner-update tests prove admission, not runner disappearance or a deadline
that competes with cutover. These tests reuse its release/provider fixtures and
prove those new risks without another host or browser.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from tests.unit.test_deployment import SHA_A, SHA_B
from tests.unit.test_owner_updates import Provider, release

from planner.core.db import connect, create_schema
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
from planner.environments.logic.update_experience import experience, settlement
from planner.environments.update_contracts import OwnerUpdateRequest
from planner.environments.update_requests import FileUpdateRequestStore, UpdateConflict
from planner.environments.update_service import UpdateService


def request(*, state: str = "applying", elapsed: bool = False) -> OwnerUpdateRequest:
    from typing import cast

    from planner.environments.update_contracts import UpdateRequestState

    now = datetime.now(UTC)
    return OwnerUpdateRequest(
        "request", release(), "now", cast(UpdateRequestState, state), now.isoformat(),
        dispatched_at=(now - timedelta(minutes=16)).isoformat(),
        deadline_at=(now + timedelta(minutes=-1 if elapsed else 15)).isoformat(),
        deployment_id="deployment",
    )


def service(tmp_path: Path, receipt: OwnerUpdateRequest, *, sha: str = SHA_A) -> UpdateService:
    store = FileUpdateRequestStore(tmp_path / "request.json")
    store.create(receipt)
    return UpdateService(
        Provider(), store, lambda: None,
        connection_factory=lambda: connect(str(tmp_path / "state.db")),
        current_app_sha=sha,
        lifecycle_store=DeploymentLifecycleStore(tmp_path / "lifecycle.json"),
    )


@pytest.mark.parametrize("missing_report", [False, True])
def test_settlement_uses_matching_health_and_current_sha(
    tmp_path: Path, missing_report: bool,
) -> None:
    updates = service(tmp_path, request(elapsed=True), sha=SHA_B)
    progress = updates.lifecycle_store
    assert progress is not None
    progress.start("deployment", SHA_B, prior_sha=SHA_A)
    progress.transition("deployment", "verifying")
    progress.transition("deployment", "app_healthy", serving_sha=SHA_B)
    if missing_report:
        progress.transition("deployment", "failed", serving_sha=SHA_B, detail="Report failed.")
    status = updates.status()
    assert status.request is not None and status.request.state == "applied"
    assert status.app_sha == SHA_B
    # An old runner cannot reopen a server-settled outcome.
    with pytest.raises(UpdateConflict):
        updates.store.transition("request", frozenset({"applied"}), "applying")


def test_killed_runner_deadline_settles_once_and_does_not_block_retry(tmp_path: Path) -> None:
    updates = service(tmp_path, request(elapsed=True))
    status = updates.status()
    assert status.state == "failed" and status.allowed_actions == ("retry",)
    assert status.request is not None and status.request.state == "failed"
    new = updates.request(release(), "now", confirm_activity=True)
    assert new.request_id != "request"
    with pytest.raises(UpdateConflict, match="authority"):
        with updates.store.installation_guard("request"):
            pytest.fail("stale runner entered cutover")


def test_failed_recovery_settles_without_reopening_applying(tmp_path: Path) -> None:
    updates = service(tmp_path, request())
    progress = updates.lifecycle_store
    assert progress is not None
    progress.start("deployment", SHA_B, prior_sha=SHA_A)
    progress.transition("deployment", "failed", serving_sha=SHA_A, detail="Prior app recovered.")
    assert updates.status().request.state == "failed"  # type: ignore[union-attr]
    assert not updates.dispatch_pending()


def test_expired_wait_ends_while_activity_is_unknown_and_promote_preserves_identity(
    tmp_path: Path,
) -> None:
    receipt = replace(request(state="pending"), mode="idle", dispatched_at=None, deadline_at=None)
    updates = service(tmp_path, receipt)
    promoted = updates.promote(receipt.request_id, confirm_activity=True)
    assert promoted.request_id == receipt.request_id
    assert promoted.mode == "now" and promoted.confirm_activity
    updates.cancel(receipt.request_id)
    expired = replace(
        receipt, request_id="expired",
        release=replace(release(), expires_at="2020-01-01T00:00:00Z"),
    )
    updates.store.create(expired)
    assert not updates.dispatch_pending()
    assert updates.store.read().state == "failed"  # type: ignore[union-attr]
    assert updates.status().allowed_actions == ("retry",)  # Latest publication remains usable.


def test_guard_refuses_expired_authority_even_without_a_server_poll(tmp_path: Path) -> None:
    updates = service(tmp_path, request(elapsed=True))
    with pytest.raises(UpdateConflict, match="expired"):
        with updates.store.installation_guard("request"):
            pytest.fail("expired authority entered cutover")


def test_same_sha_recovery_uses_integrity_audit_without_opening_normal_work(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    connection = connect(str(database))
    create_schema(connection)
    connection.close()
    store = FileUpdateRequestStore(tmp_path / "request.json")
    updates = UpdateService(
        Provider(), store, lambda: None, current_app_sha=SHA_B,
        connection_factory=lambda: connect(str(database)),
        recovery_admission=lambda: True,
    )
    assert updates.status().available is not None
    receipt = updates.request(release(), "now", confirm_activity=True)
    assert updates.dispatch_pending()
    claimed = asyncio.run(updates.claim(receipt.request_id, deployment_id="recovery"))
    assert claimed.state == "applying" and claimed.deployment_id == "recovery"
    assert claimed.deadline_at is not None
    payload = json.loads((tmp_path / "request.json").read_text())
    assert payload["deadline_at"] == claimed.deadline_at


def test_legacy_deadline_never_restarts_its_budget_and_other_deployments_expire(
    tmp_path: Path,
) -> None:
    old = replace(
        request(), created_at="2020-01-01T00:00:00Z", dispatched_at=None, deadline_at=None,
    )
    assert settlement(old, None, SHA_A, datetime.now(UTC))[0] == "failed"  # type: ignore[index]
    lifecycle = DeploymentLifecycleStore(tmp_path / "lifecycle.json")
    started = lifecycle.start("ordinary", SHA_B, prior_sha=SHA_A)
    expired = replace(started, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert experience(None, expired, release(), True, SHA_A, datetime.now(UTC)) == (
        "failed", ("retry",),
    )
    assert asdict(old)["deadline_at"] is None


def test_later_ordinary_deployment_supersedes_a_terminal_owner_receipt(tmp_path: Path) -> None:
    old = replace(request(state="failed"), detail="An old request failed.")
    lifecycle = DeploymentLifecycleStore(tmp_path / "lifecycle.json")
    preparing = lifecycle.start("ordinary", SHA_B, prior_sha=SHA_A)
    assert experience(old, preparing, release(), True, SHA_A, datetime.now(UTC)) == (
        "updating", (),
    )
    restarting = lifecycle.transition("ordinary", "restarting")
    old = replace(old, deadline_at="2020-01-01T00:00:00Z")
    updates = service(tmp_path, old)
    updates.lifecycle_store = lifecycle
    status = updates.status()
    assert status.deadline_at is None
    assert status.restart_deadline_at == restarting.expires_at.isoformat()
    assert status.detail != old.detail
    failed = lifecycle.transition("ordinary", "failed", detail="The later deployment failed.")
    assert updates.status().detail == failed.detail
    assert experience(old, restarting, release(), True, SHA_A, datetime.now(UTC)) == (
        "restarting", (),
    )
    # A newly active request cannot inherit that unrelated restart.
    active = replace(old, state="dispatched", created_at=datetime.now(UTC).isoformat())
    assert experience(active, restarting, release(), True, SHA_A, datetime.now(UTC)) == (
        "updating", (),
    )


def test_settlement_reads_health_after_the_authority_guard_releases(tmp_path: Path) -> None:
    import threading

    updates = service(tmp_path, request(), sha=SHA_B)
    progress = updates.lifecycle_store
    assert progress is not None
    progress.start("deployment", SHA_B, prior_sha=SHA_A)
    progress.transition("deployment", "verifying")
    attempted = threading.Event()
    finished = threading.Event()
    results: list[str] = []

    def poll() -> None:
        attempted.set()
        receipt = updates.reconcile()
        assert receipt is not None
        results.append(receipt.state)
        finished.set()

    with updates.store.installation_guard("request"):
        thread = threading.Thread(target=poll)
        thread.start()
        assert attempted.wait(1)
        assert not finished.is_set()
        progress.transition("deployment", "app_healthy", serving_sha=SHA_B)
    thread.join(timeout=2)
    assert finished.is_set() and results == ["applied"]


def test_replacement_does_not_erase_uncommitted_candidate_admission(tmp_path: Path) -> None:
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app
    from planner.environments.update_admission import UncommittedAppStore

    database = tmp_path / "state.db"
    connection = connect(str(database))
    create_schema(connection)
    connection.close()

    def candidate(sha: str) -> FastAPI:
        config = load_config(path=None, env={
            "PLAN_TEST_MODE": "1", "PLAN_APP_SHA": sha, "PLAN_DB_PATH": str(database),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        })
        return create_app(config, build_clock(config), lambda: connect(str(database)))

    store = FileUpdateRequestStore(tmp_path / "owner-update-request.json")
    store.create(request())
    # B starts without a matching health commit. This writes an independent hold.
    assert candidate(SHA_B).state.update_startup_pending
    store.transition("request", frozenset({"applying"}), "failed")
    target_c = "c" * 40
    replacement = replace(
        request(state="pending"), request_id="replacement",
        release=replace(release(), manifest=replace(release().manifest, app_sha=target_c)),
    )
    store.create(replacement)
    lifecycle = DeploymentLifecycleStore(tmp_path / "deployment-lifecycle.json")
    lifecycle.start("replacement", target_c, prior_sha=SHA_B)
    # A reboot before cutover cannot infer B health from C's request/lifecycle.
    assert candidate(SHA_B).state.update_startup_pending
    lifecycle.transition("replacement", "restarting")
    assert candidate(target_c).state.update_startup_pending
    lifecycle.transition("replacement", "rolled_back", serving_sha=SHA_B)
    # C failure and rollback cannot erase B's original admission requirement.
    assert candidate(SHA_B).state.update_startup_pending
    holds = UncommittedAppStore(tmp_path / "update-admission.json")
    assert holds.contains(SHA_B) and holds.contains(target_c)
    holds.commit(SHA_B)
    assert not holds.contains(SHA_B) and holds.contains(target_c)
