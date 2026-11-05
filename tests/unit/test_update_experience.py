"""One server answer covers retained update actions and the installed version.

These cases reuse owner-update fixtures. They prove idle promotion and lifecycle
selection without a new host, browser, or the removed deadline machinery.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from tests.unit.test_deployment import SHA_A, SHA_B
from tests.unit.test_owner_updates import Provider, release

from planner.core.db import connect
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
from planner.environments.logic.update_experience import experience
from planner.environments.update_contracts import OwnerUpdateRequest
from planner.environments.update_requests import FileUpdateRequestStore
from planner.environments.update_service import UpdateService


def service(tmp_path: Path, receipt: OwnerUpdateRequest, *, sha: str = SHA_A) -> UpdateService:
    store = FileUpdateRequestStore(tmp_path / "request.json")
    store.create(receipt)
    return UpdateService(
        Provider(), store, lambda: None,
        connection_factory=lambda: connect(str(tmp_path / "state.db")),
        current_app_sha=sha,
        lifecycle_store=DeploymentLifecycleStore(tmp_path / "lifecycle.json"),
    )


def test_idle_promotion_preserves_request_identity_and_confirmation(tmp_path: Path) -> None:
    receipt = OwnerUpdateRequest(
        "request", release(), "idle", "requested", datetime.now(UTC).isoformat(),
    )
    updates = service(tmp_path, receipt)
    assert updates.status().state == "waiting"
    assert updates.status().allowed_actions == ("cancel", "update_now")
    promoted = updates.promote(receipt.request_id, confirm_activity=True)
    assert promoted.request_id == receipt.request_id
    assert promoted.mode == "now" and promoted.confirm_activity
    assert updates.status().state == "updating"
    assert updates.status().allowed_actions == ()


def test_later_ordinary_deployment_supersedes_terminal_receipt(tmp_path: Path) -> None:
    old = OwnerUpdateRequest(
        "request", release(), "now", "failed", "2020-01-01T00:00:00Z",
        detail="An old request failed.",
    )
    lifecycle = DeploymentLifecycleStore(tmp_path / "lifecycle.json")
    preparing = lifecycle.start("ordinary", SHA_B, prior_sha=SHA_A)
    assert experience(old, preparing, release(), True, SHA_A, datetime.now(UTC)) == (
        "updating", (),
    )
    restarting = lifecycle.transition("ordinary", "restarting")
    assert experience(old, restarting, release(), True, SHA_A, datetime.now(UTC)) == (
        "restarting", (),
    )
    active = replace(old, state="installing", created_at=datetime.now(UTC).isoformat())
    assert experience(active, restarting, release(), True, SHA_A, datetime.now(UTC)) == (
        "updating", (),
    )


def test_installed_version_and_failed_request_keep_distinct_actions(tmp_path: Path) -> None:
    receipt = OwnerUpdateRequest(
        "request", release(), "now", "failed", datetime.now(UTC).isoformat(),
    )
    assert service(tmp_path / "failed", receipt).status().allowed_actions == ("retry",)
    installed = service(tmp_path / "installed", replace(receipt, state="done"), sha=SHA_B)
    assert installed.status().available is None
    assert installed.status().state == "up_to_date"
    assert installed.status().allowed_actions == ()
