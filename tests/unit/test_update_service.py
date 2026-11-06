"""One press, its background start, and the owner routes, against a real attempt file.

The installer itself is proved in tests/integration/test_update_installer.py.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.update_apps import SHA_A, SHA_B, build_app, release

from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.db import connect, create_schema
from planner.environments.update_api import router
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    UpdateConflict,
    import_legacy_request,
)
from planner.environments.update_contracts import PublishedMacUpdate, PublishedUpdate
from planner.environments.update_service import ACTIVITY_CHANGED_DETAIL, UpdateService


class System:
    """The two conversation-system calls an update uses, over the real admission barrier."""

    def __init__(self, activity: str = "idle") -> None:
        self.workspace_admission = WorkspaceAdmission()
        self.activity = activity

    def workspace_activity(self) -> str:
        return "unknown" if self.workspace_admission.closed else self.activity

    async def run_with_workspace_admission(
        self, operation: Callable[[], None], *, require_idle: bool
    ) -> bool:
        return self.workspace_admission.claim(
            operation, idle=lambda: self.activity == "idle", require_idle=require_idle
        )


class Provider:
    def __init__(self, offered: PublishedUpdate) -> None:
        self.offered = offered

    def available(self) -> PublishedUpdate:
        return self.offered

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        raise AssertionError("staging is replaced in these tests")


class Harness:
    def __init__(self, root: Path, *, migrated: bool = True) -> None:
        self.root = root
        (root / "data").mkdir(parents=True)
        self.database = root / "data" / "planner.db"
        connection = connect(str(self.database))
        create_schema(connection)
        connection.close()
        staged = build_app(root / f".app-next-{SHA_B}", SHA_B, "new")
        versions = staged / "src/planner/core/migrations/versions"
        versions.mkdir(parents=True)
        with sqlite3.connect(self.database) as db:
            head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        (versions / "base.py").write_text(f'revision = "{head}"\ndown_revision = None\n')
        if not migrated:
            # The candidate gains a revision this database has not applied.
            (versions / "next.py").write_text(f'revision = "next"\ndown_revision = "{head}"\n')
        self.release = release(mac=PublishedMacUpdate(9, "f" * 64, 10))
        self.store = FileUpdateAttemptStore(root / "data" / "update-attempt.json")
        self.system = System()
        self.launched: list[tuple[str, list[str]]] = []
        self.launch_error: Exception | None = None
        self.service = UpdateService(
            Provider(self.release),
            self.store,
            lambda: self.system,  # type: ignore[arg-type,return-value]
            connection_factory=lambda: connect(str(self.database)),
            current_root=root,
            database=self.database,
            backup_directory=root / "data" / "backups",
            current_app_sha=SHA_A,
            health_url="http://127.0.0.1:9/api/health",
            service_unit="panels-test.service",
            launch=self.launch,
            stage=lambda provider, published, current_root: staged,
        )
        self.service.prefetch()

    def launch(self, attempt_id: str, command: list[str]) -> None:
        if self.launch_error is not None:
            raise self.launch_error
        self.launched.append((attempt_id, command))

    def advance(self) -> None:
        asyncio.run(self.service.advance())


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path / "current")


def test_press_answers_at_once_then_pauses_work_and_starts_the_installer(
    harness: Harness,
) -> None:
    """The request path only records; the background part pauses work and launches the unit."""
    attempt = harness.service.install(SHA_B, "now", confirm_activity=False)
    assert (attempt.state, attempt.from_sha, attempt.to_sha) == ("waiting", SHA_A, SHA_B)
    assert attempt.mac_attempt_id and attempt.mac_attempt_id.isalnum()
    assert not harness.launched
    harness.advance()
    recorded = harness.store.read()
    assert recorded is not None and recorded.state == "installing"
    assert recorded.backup is None  # the database is already at the candidate's head
    assert recorded.deadline_at is not None
    assert {"pressed", "work_paused", "installing"} <= set(recorded.steps)
    assert harness.system.workspace_admission.closed
    [(attempt_id, command)] = harness.launched
    assert attempt_id == attempt.attempt_id
    assert command[1:4] == ["-I", "-S", "-B"]
    assert Path(command[4]) == harness.root / "data" / "update-installer.py"
    assert Path(command[4]).read_text().startswith('"""')
    assert command[5:] == [
        "--current-root", str(harness.root), "--attempt-id", attempt.attempt_id,
        "--service", "panels-test.service", "--health-url", "http://127.0.0.1:9/api/health",
        "--health-timeout-seconds", "300.0",
    ]
    with pytest.raises(UpdateConflict):
        harness.service.install(SHA_B, "now", confirm_activity=True)


def test_schema_change_takes_the_backup_before_the_installer_starts(tmp_path: Path) -> None:
    """Only a migrating release pays for a backup, and it is taken while Panels still runs."""
    harness = Harness(tmp_path / "current", migrated=False)
    harness.service.install(SHA_B, "now", confirm_activity=False)
    harness.advance()
    recorded = harness.store.read()
    assert recorded is not None and recorded.backup is not None
    snapshot = Path(recorded.backup)
    assert snapshot.parent == harness.root / "data" / "backups"
    assert json.loads((snapshot / "metadata.json").read_text())["deployed_revision"] == SHA_A
    assert "backup" in recorded.steps and harness.launched


def test_idle_wait_cancel_and_update_now(harness: Harness) -> None:
    """Waiting holds until idle; cancel removes it; Update now needs confirmation."""
    harness.system.activity = "busy"
    waiting = harness.service.install(SHA_B, "idle", confirm_activity=False)
    harness.advance()
    assert harness.store.read() == waiting and not harness.launched
    assert harness.service.poll() is False
    harness.service.cancel(waiting.attempt_id)
    assert harness.store.read() is None
    second = harness.service.install(SHA_B, "idle", confirm_activity=False)
    with pytest.raises(UpdateConflict):
        harness.service.update_now(second.attempt_id, confirm_activity=False)
    harness.service.update_now(second.attempt_id, confirm_activity=True)
    harness.advance()
    assert harness.launched and harness.store.read().state == "installing"  # type: ignore[union-attr]
    with pytest.raises(UpdateConflict):
        harness.service.cancel(second.attempt_id)


def test_refusals_before_stop_fail_plainly_and_reopen_work(harness: Harness) -> None:
    """Nothing that fails before the installer runs may leave work paused."""
    harness.system.activity = "busy"
    with pytest.raises(UpdateConflict, match="confirmation"):
        harness.service.install(SHA_B, "now", confirm_activity=False)
    # Work started between an idle press and its background start.
    harness.system.activity = "idle"
    first = harness.service.install(SHA_B, "now", confirm_activity=False)
    harness.system.activity = "busy"
    harness.advance()
    assert harness.store.read() == replace(
        first, state="failed", detail=ACTIVITY_CHANGED_DETAIL,
        updated_at=harness.store.read().updated_at,  # type: ignore[union-attr]
    )
    assert not harness.system.workspace_admission.closed
    harness.system.activity = "idle"
    harness.launch_error = OSError("no user manager")
    retried = harness.service.retry(first.attempt_id)
    assert retried.mode == "now" and retried.confirm_activity
    harness.advance()
    failed = harness.store.read()
    assert failed is not None and failed.state == "failed"
    assert failed.detail and "no user manager" in failed.detail
    assert not harness.system.workspace_admission.closed


def test_ticket_integrity_refusal_keeps_work_open(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The integrity audit runs under the barrier, and its refusal never closes it."""
    def refuse(*args: object, **kwargs: object) -> None:
        raise ValueError("Ticket t1 names a missing stage")

    monkeypatch.setattr(
        "planner.environments.update_service.audit_ticket_registry_integrity", refuse
    )
    harness.service.install(SHA_B, "now", confirm_activity=False)
    harness.advance()
    failed = harness.store.read()
    assert failed is not None and failed.state == "failed"
    assert failed.detail and "Ticket t1 names a missing stage" in failed.detail
    assert not harness.system.workspace_admission.closed and not harness.launched


def test_retry_mac_gives_the_mac_a_new_target_and_settled_attempts_do_not_block(
    harness: Harness,
) -> None:
    """Try again for the Mac is a fresh mac_attempt_id on the same done attempt."""
    attempt = harness.service.install(SHA_B, "now", confirm_activity=False)
    harness.advance()
    done = harness.store.change(
        attempt.attempt_id, frozenset({"installing"}),
        lambda current: replace(current, state="done"),
    )
    retried = harness.service.retry_mac(attempt.attempt_id)
    assert retried.mac_attempt_id != done.mac_attempt_id
    assert retried.to_sha == done.to_sha and retried.state == "done"
    harness.service.install(SHA_B, "idle", confirm_activity=False)


def test_legacy_request_becomes_a_done_attempt_for_the_waiting_mac(tmp_path: Path) -> None:
    """Bridge: a Mac the 5622d2a81 server stopped gets activate for its old request id."""
    data = tmp_path / "data"
    data.mkdir()
    store = FileUpdateAttemptStore(data / "update-attempt.json")
    published = release(mac=PublishedMacUpdate(9, "f" * 64, 10))
    legacy = {
        "request_id": "legacy1", "release": asdict(published), "mode": "now",
        "state": "applying", "created_at": "2026-11-05T10:00:00+00:00",
        "confirm_activity": True, "dispatched_at": None, "deadline_at": None,
        "deployment_id": "legacy1",
    }
    (data / "owner-update-request.json").write_text(json.dumps(legacy))
    (data / "deployment-lifecycle.json").write_text(json.dumps({
        "requested_sha": SHA_B, "prior_sha": SHA_A, "phase": "restarting",
    }))
    assert import_legacy_request(store, data, SHA_A) is None  # not the running release
    imported = import_legacy_request(store, data, SHA_B)
    assert imported is not None
    assert (imported.attempt_id, imported.mac_attempt_id, imported.state) == (
        "legacy1", "legacy1", "done"
    )
    assert (imported.from_sha, imported.to_sha, imported.release) == (SHA_A, SHA_B, published)
    assert store.read() == imported
    # Once only: a second start, or the old runner's later "applied", changes nothing.
    legacy["state"] = "applied"
    (data / "owner-update-request.json").write_text(json.dumps(legacy))
    assert import_legacy_request(store, data, SHA_B) is None
    assert store.read() == imported


def test_attempt_writes_keep_fields_this_version_does_not_know(harness: Harness) -> None:
    """The record crosses versions: a newer writer's fields survive an older writer."""
    attempt = harness.service.install(SHA_B, "idle", confirm_activity=False)
    payload = json.loads(harness.store.path.read_text())
    payload["future_field"] = {"kept": True}
    harness.store.path.write_text(json.dumps(payload))
    harness.service.update_now(attempt.attempt_id, confirm_activity=True)
    assert json.loads(harness.store.path.read_text())["future_field"] == {"kept": True}


def test_routes_and_status_shape(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """The card's API names and shape are the contract the web client builds against."""
    monkeypatch.setattr("planner.environments.update_api.require_owner", lambda context: None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.updates = harness.service
    with TestClient(app) as client:
        body = client.get("/api/updates").json()
        assert set(body) == {
            "state", "allowed_actions", "activity", "available", "attempt", "app_sha",
            "detail", "deadline_at", "mac_details",
        }
        assert (body["state"], body["allowed_actions"], body["attempt"]) == (
            "available", ["update"], None
        )
        assert body["available"]["app_sha"] == SHA_B and body["app_sha"] == SHA_A
        assert body["mac_details"] is None
        response = client.post(
            "/api/updates/install", json={"app_sha": SHA_B, "mode": "idle"}
        )
        assert response.status_code == 200
        attempt_id = response.json()["attempt"]["attempt_id"]
        assert set(response.json()["attempt"]) == {
            "attempt_id", "from_sha", "to_sha", "mode", "state", "detail", "deadline_at",
        }
        harness.system.activity = "busy"
        assert client.get("/api/updates").json()["allowed_actions"] == ["cancel", "update_now"]
        assert client.post(f"/api/updates/attempts/{attempt_id}/now", json={}).status_code == 409
        assert client.post(f"/api/updates/attempts/{attempt_id}/retry").status_code == 409
        assert client.post(f"/api/updates/attempts/{attempt_id}/retry-mac").status_code == 409
        assert client.post(f"/api/updates/attempts/{attempt_id}/cancel").json() == {
            "attempt": None
        }
        assert client.get("/api/updates").json()["state"] == "available"


@pytest.mark.parametrize("ending", ["installer_refused", "deadline"])
def test_an_attempt_that_ends_while_this_process_runs_reopens_work(
    harness: Harness, ending: str
) -> None:
    """The installer stops this process when it swaps; if it never did, work must reopen."""
    attempt = harness.service.install(SHA_B, "now", confirm_activity=False)
    harness.advance()
    assert harness.system.workspace_admission.closed
    assert harness.service.poll() is True  # still installing: work stays paused
    assert harness.system.workspace_admission.closed
    if ending == "installer_refused":
        harness.store.change(
            attempt.attempt_id, frozenset({"installing"}),
            lambda current: replace(current, state="failed", detail="missing release"),
        )
    else:
        harness.store.change(
            attempt.attempt_id, frozenset({"installing"}),
            lambda current: replace(current, deadline_at="2000-01-01T00:00:00+00:00"),
        )
    assert harness.service.poll() is False
    recorded = harness.store.read()
    assert recorded is not None and recorded.state == "failed"
    assert not harness.system.workspace_admission.closed
    assert harness.system.workspace_activity() == "idle"


def test_prefetch_waits_while_an_attempt_owns_the_staged_release(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A newer publication cannot replace the staged folder an unsettled attempt installs."""
    def refuse(**kwargs: object) -> None:
        pytest.fail("prefetch started during an unsettled attempt")

    monkeypatch.setattr("planner.environments.update_service.threading.Thread", refuse)
    harness.service.install(SHA_B, "idle", confirm_activity=False)
    harness.service.refresh_available(force=True)


def test_installer_restarts_only_the_unit_this_server_runs_in() -> None:
    # A wrong name here stops another service, so the parse must name this unit or none.
    from planner.environments.update_install import own_service_unit

    live = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/panels-live.service\n"
    assert own_service_unit(live) == "panels-live.service"
    scratch = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/panels-scratch.service\n"
    assert own_service_unit(scratch) == "panels-scratch.service"
    terminal = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/vte-spawn-1.scope\n"
    assert own_service_unit(terminal) is None
    assert own_service_unit("0::/user.slice/user-1000.slice/user@1000.service\n") is None
    assert own_service_unit("") is None
