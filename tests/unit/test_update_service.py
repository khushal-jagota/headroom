"""One press, its background start, and the owner routes, against a real attempt file.

The installer itself is proved in tests/integration/test_update_installer.py.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.update_apps import SHA_A, SHA_B, build_app, release

from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.db import connect, create_schema
from planner.environments.app import AppManifest
from planner.environments.runtime_contracts import NodeRuntimeContract
from planner.environments.update_api import router
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    UpdateConflict,
    import_legacy_request,
)
from planner.environments.update_barrier import UpdateBarrier
from planner.environments.update_contracts import PublishedMacUpdate, PublishedUpdate
from planner.environments.update_install import require_exact_schema_frontier
from planner.environments.update_service import ACTIVITY_CHANGED_DETAIL, WAIT_DETAIL, UpdateService


class System:
    """The two conversation-system calls an update uses, over the real admission barrier."""

    def __init__(self, activity: str = "idle") -> None:
        self.workspace_admission = WorkspaceAdmission()
        self.activity = activity

    def workspace_activity(self) -> str:
        return "unknown" if self.workspace_admission.closed else self.activity

    async def prepare_update(self, mode: str) -> bool:
        self.workspace_admission.close_starts()
        if self.workspace_admission.active or (mode == "idle" and self.activity != "idle"):
            return False
        if not self.workspace_admission.closed:
            return self.workspace_admission.claim(
                lambda: None, idle=lambda: True, require_idle=True,
            )
        return True

    def cancel_update(self) -> bool:
        self.workspace_admission.reopen()
        return True

    def update_waiting_work(self) -> tuple[str, ...]:
        return ("Example Worker",) if self.activity != "idle" else ()


class Provider:
    def __init__(self, offered: PublishedUpdate) -> None:
        self.offered = offered

    def available(self) -> PublishedUpdate:
        return self.offered

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        raise AssertionError("staging is replaced in these tests")


class Harness:
    def __init__(self, root: Path, *, migrated: bool = True, node: bool = False) -> None:
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
        self.release = release(
            AppManifest(SHA_B, "c" * 64, "d" * 64, NodeRuntimeContract("f" * 64)) if node else None,
            mac=PublishedMacUpdate(9, "f" * 64, 10),
        )
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


@pytest.mark.parametrize("native_snapshot", [False, True])
def test_required_snapshot_precedes_installer_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_snapshot: bool
) -> None:
    """Mac trials also need a restorable record when their schema stays unchanged."""
    from planner.platform_runtime.linux.adapter import LinuxPlatformAdapter

    monkeypatch.setattr(LinuxPlatformAdapter, "update_requires_snapshot", native_snapshot)
    harness = Harness(tmp_path / "current", migrated=native_snapshot)
    from planner.environments import update_service

    backup = update_service.create_database_backup

    def protected_snapshot(*args, **kwargs):
        current = harness.store.read()
        assert current is not None and harness.service.owns_preparation(current.attempt_id)
        return backup(*args, **kwargs)

    monkeypatch.setattr(update_service, "create_database_backup", protected_snapshot)
    harness.service.install(SHA_B, "now", confirm_activity=False)
    harness.advance()
    recorded = harness.store.read()
    assert recorded is not None and recorded.backup is not None
    snapshot = Path(recorded.backup)
    assert snapshot.parent == harness.root / "data" / "backups"
    assert json.loads((snapshot / "metadata.json").read_text())["deployed_revision"] == SHA_A
    assert "backup" in recorded.steps and harness.launched



def test_native_snapshot_policy_still_refuses_unknown_schema(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unconditional snapshot must not admit an incompatible database revision."""
    from planner.platform_runtime.linux.adapter import LinuxPlatformAdapter

    monkeypatch.setattr(LinuxPlatformAdapter, "update_requires_snapshot", True)
    with sqlite3.connect(harness.database) as db:
        db.execute("UPDATE alembic_version SET version_num = 'unknown_revision'")
    harness.service.install(SHA_B, "now", confirm_activity=False)
    harness.advance()
    attempt = harness.store.read()
    assert attempt is not None and attempt.state == "failed"
    assert attempt.detail is not None and "schema revision" in attempt.detail
    assert not harness.launched and attempt.backup is None
    assert not harness.system.workspace_admission.closed


def test_node_release_needs_the_exact_schema_frontier_at_staging(tmp_path: Path) -> None:
    """Risk: a Node release, which runs no migration, is offered for a database behind it."""
    with pytest.raises(ValueError, match="exactly its schema revision"):
        Harness(tmp_path / "behind", migrated=False, node=True)
    ready = Harness(tmp_path / "exact", node=True)
    assert ready.service.ready_release() == ready.release
    # A candidate without migrations and a database without a revision do not match.
    empty = tmp_path / "empty.db"
    with sqlite3.connect(empty) as db:
        db.execute("CREATE TABLE alembic_version (version_num TEXT)")
    with pytest.raises(ValueError, match="exactly its schema revision"):
        require_exact_schema_frontier(empty, tmp_path / "no-migrations")


@pytest.mark.parametrize("frontier", ["ancestor", "unknown", "divergent"])
def test_node_release_rechecks_the_frontier_before_the_stop(
    tmp_path: Path, frontier: str
) -> None:
    """Risk: the frontier differs at install time, and the service stops for a release
    that cannot run on it. A known ancestor, which version 1 backs up and migrates,
    is refused too."""
    harness = Harness(tmp_path / "current", node=True)
    harness.service.install(SHA_B, "now", confirm_activity=False)
    with sqlite3.connect(harness.database) as db:
        head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if frontier == "ancestor":
            staged = harness.root / f".app-next-{SHA_B}/src/planner/core/migrations/versions"
            (staged / "next.py").write_text(f'revision = "next"\ndown_revision = "{head}"\n')
        else:
            db.execute("DELETE FROM alembic_version")
            db.executemany(
                "INSERT INTO alembic_version VALUES (?)",
                [("unknown",)] + ([(head,)] if frontier == "divergent" else []),
            )
    harness.advance()
    recorded = harness.store.read()
    assert recorded is not None and recorded.state == "failed"
    assert "exactly its schema revision" in (recorded.detail or "")
    assert recorded.backup is None and not harness.launched


def test_idle_update_advances_after_reconcilers_finish_without_another_poll(
    harness: Harness,
) -> None:
    """A queued check can still be busy; the operation's exit must wake it again."""
    harness.system.activity = "busy"
    waiting = harness.service.install(SHA_B, "idle", confirm_activity=False)
    harness.service.cancel(waiting.attempt_id)

    async def run() -> None:
        harness.service.start(asyncio.get_running_loop())
        await asyncio.gather(*(
            asyncio.wrap_future(task) for task in tuple(harness.service._tasks)
        ))
        with harness.system.workspace_admission.operation() as admitted:
            assert admitted
            harness.store.create(waiting)
            assert not harness.launched
            assert harness.service.poll() is True
            await asyncio.gather(*(
                asyncio.wrap_future(task) for task in tuple(harness.service._tasks)
            ))
            assert harness.store.read().state == "waiting"  # type: ignore[union-attr]
            harness.system.activity = "idle"
        # No additional poll, database commit, or explicit advance follows release.
        await asyncio.sleep(0)
        await asyncio.gather(*(
            asyncio.wrap_future(task) for task in tuple(harness.service._tasks)
        ))
        assert harness.launched
        harness.service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "stop"])
def test_ended_update_ignores_copied_work_completion_callbacks(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    """A callback already copied by a worker cannot revive a canceled or stopped owner."""
    harness.system.activity = "busy"
    waiting = harness.service.install(SHA_B, "idle", confirm_activity=False)
    callbacks: list[Callable[[], None]] = []
    subscribe = harness.system.workspace_admission.subscribe_operations_finished

    def observe(callback: Callable[[], None]) -> Callable[[], None]:
        callbacks.append(callback)
        return subscribe(callback)

    monkeypatch.setattr(
        harness.system.workspace_admission, "subscribe_operations_finished", observe,
    )

    async def run() -> None:
        harness.service.start(asyncio.get_running_loop())
        await asyncio.gather(*(
            asyncio.wrap_future(task) for task in tuple(harness.service._tasks)
        ))
        harness.system.activity = "idle"
        harness.service.schedule_advance()
        queued = tuple(harness.service._tasks)
        if action == "cancel":
            harness.service.cancel(waiting.attempt_id)
        else:
            harness.service.stop()
        callbacks[0]()  # Simulate delivery after unsubscribe or cancellation.
        await asyncio.gather(*(asyncio.wrap_future(task) for task in queued))
        await asyncio.sleep(0)
        assert not harness.service._tasks and not harness.launched
        harness.service.stop()

    asyncio.run(run())


def test_unexpected_background_admission_error_reaches_the_home_log(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Discarding a concurrent future must not hide the cause of a waiting attempt."""
    harness.service.install(SHA_B, "idle", confirm_activity=False)

    async def broken(mode: str) -> bool:
        raise OSError("admission diagnostic receipt")

    monkeypatch.setattr(harness.system, "prepare_update", broken)

    async def run() -> None:
        harness.service.start(asyncio.get_running_loop())
        await asyncio.gather(*(
            asyncio.wrap_future(task) for task in tuple(harness.service._tasks)
        ), return_exceptions=True)
        harness.service.stop()

    asyncio.run(run())
    assert "admission diagnostic receipt" in (harness.store.read().detail or "")
    assert harness.service.status().state == "waiting"
    assert not harness.launched


@pytest.fixture(params=["linux", "darwin"])
def update_platform(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Select the OS policy, while the existing harness captures installer launch."""
    from planner.platform_runtime.darwin.adapter import DarwinPlatformAdapter
    from planner.platform_runtime.linux.adapter import LinuxPlatformAdapter

    adapter = DarwinPlatformAdapter() if request.param == "darwin" else LinuxPlatformAdapter()
    # Host authentication is a native proof. These cases prove the common owner
    # policy through the selected adapter, without asking launchd about a Linux test.
    monkeypatch.setattr(adapter, "installer_python", lambda root: Path("/usr/bin/python3"))
    monkeypatch.setattr("planner.environments.update_service.get_platform_adapter", lambda: adapter)
    monkeypatch.setattr("planner.environments.update_install.get_platform_adapter", lambda: adapter)


def test_idle_wait_cancel_and_update_now(harness: Harness, update_platform: None) -> None:
    """Waiting holds until idle; cancel removes it; Update now needs confirmation."""
    harness.system.activity = "busy"
    waiting = harness.service.install(SHA_B, "idle", confirm_activity=False)
    harness.advance()
    assert harness.store.read() == waiting and not harness.launched
    assert harness.service.poll() is True
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


def test_idle_wait_reports_the_bound_without_forcing_or_latching_work(
    harness: Harness, update_platform: None
) -> None:
    """An overdue idle wait must inform the owner without forcing or latching work."""
    harness.system.activity = "busy"
    waiting = harness.service.install(SHA_B, "idle", confirm_activity=False)
    assert waiting.deadline_at is not None
    harness.advance()
    assert harness.system.workspace_admission.starts_closed
    assert not harness.system.workspace_admission.closed
    later = time.time() + 301
    harness.service._now = lambda: later
    harness.advance()
    status = harness.service.status()
    assert status.state == "waiting" and (status.detail or "").startswith(WAIT_DETAIL)
    assert status.allowed_actions == ("cancel", "update_now")
    assert "Example Worker" in (status.detail or "")
    assert harness.store.read().deadline_at is None
    harness.advance()
    assert not harness.launched  # The bound never turns an idle wait into a forced stop.
    harness.system.activity = "idle"
    harness.advance()
    recorded = harness.store.read()
    assert recorded is not None and recorded.state == "installing" and harness.launched


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


def test_cancel_reopens_work_after_uncertain_retirement(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unproven child stop must not launch an installer or reopen starts."""
    async def uncertain(mode: str) -> bool:
        harness.system.workspace_admission.claim(
            lambda: None, idle=lambda: True, require_idle=False,
        )
        raise RuntimeError("owned child did not stop")

    monkeypatch.setattr(harness.system, "prepare_update", uncertain)
    attempt = harness.service.install(SHA_B, "now", confirm_activity=True)
    harness.advance()
    status = harness.service.status()
    assert status.state == "waiting" and "owned child did not stop" in (status.detail or "")
    assert harness.system.workspace_admission.closed and not harness.launched
    harness.service.cancel(attempt.attempt_id)
    assert harness.store.read() is None
    assert not harness.system.workspace_admission.closed
    assert not harness.system.workspace_admission.starts_closed


def test_accepted_save_timeout_keeps_receipt_custody_and_blocks_snapshot(
    harness: Harness,
) -> None:
    """A detached accepted save can outlive the wait. Never snapshot ahead of it."""
    barrier = UpdateBarrier(harness.root / "data/update-barrier")
    harness.service._update_barrier = barrier

    async def run() -> None:
        with barrier.operation() as admitted:
            assert admitted
            attempt = harness.service.install(SHA_B, "now", confirm_activity=True)
            harness.store.change(attempt.attempt_id, frozenset({"waiting"}),
                lambda current: replace(current, deadline_at=(
                    datetime.now(UTC) + timedelta(milliseconds=50)).isoformat()))
            await harness.service.advance()
            assert barrier.frozen and not barrier.drained and not harness.launched
            status = harness.service.status()
            assert status.state == "waiting" and "result saves" in (status.detail or "")
            harness.service.cancel(attempt.attempt_id)
            assert not barrier.frozen and not harness.system.workspace_admission.closed
            assert harness.store.read() is None and not harness.launched
        assert not barrier.frozen

    asyncio.run(run())


def test_work_labels_do_not_scan_outside_an_offer_or_wait(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary readiness polls must not acquire another per-conversation label scan."""
    scans = []

    def labels() -> tuple[str, ...]:
        scans.append(True)
        return ("Example Worker",)

    monkeypatch.setattr(harness.system, "update_waiting_work", labels)
    monkeypatch.setattr(harness.service, "refresh_available", lambda: None)
    ready = harness.service._ready
    harness.service._ready = None
    assert harness.service.status().detail is None
    assert not scans
    harness.service._ready = ready
    assert "Example Worker" in (harness.service.status().detail or "")
    harness.service.install(SHA_B, "now", confirm_activity=True)
    harness.advance()
    scans.clear()
    assert harness.service.status().detail is None
    assert not scans


def test_update_reads_leave_the_loop_and_mutations_keep_the_owner(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel must not reopen admission concurrently with the preparation transition."""
    harness.system.activity = "busy"

    async def run() -> None:
        owner = asyncio.get_running_loop()
        harness.service.start(owner)
        labels = harness.system.update_waiting_work
        cancel = harness.system.cancel_update
        observations = []

        def worker_labels() -> tuple[str, ...]:
            with pytest.raises(RuntimeError, match="no running event loop"):
                asyncio.get_running_loop()
            observations.append("status")
            return labels()

        activity = harness.system.workspace_activity

        def worker_activity():
            with pytest.raises(RuntimeError, match="no running event loop"):
                asyncio.get_running_loop()
            observations.append("activity")
            return activity()

        def owner_cancel() -> bool:
            assert asyncio.get_running_loop() is owner
            observations.append("cancel")
            return cancel()

        monkeypatch.setattr(harness.system, "update_waiting_work", worker_labels)
        monkeypatch.setattr(harness.system, "workspace_activity", worker_activity)
        monkeypatch.setattr(harness.system, "cancel_update", owner_cancel)
        attempt = await asyncio.to_thread(
            harness.service.install, SHA_B, "idle", confirm_activity=False,
        )
        await asyncio.to_thread(harness.service.status)
        await asyncio.to_thread(harness.service.cancel, attempt.attempt_id)
        await asyncio.gather(*(asyncio.wrap_future(task) for task in tuple(harness.service._tasks)))
        assert {"status", "activity", "cancel"} <= set(observations)
        assert harness.store.read() is None and not harness.launched
        assert not harness.system.workspace_admission.closed
        harness.service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["stalled", "closed"])
def test_owner_controller_refusal_is_bounded_and_does_not_claim_cancellation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """The owner loop can disappear between the running check and its reply."""
    owner = asyncio.new_event_loop()
    queued = []

    class NoReply(concurrent.futures.Future):
        def result(self, timeout=None):
            assert timeout == 5
            raise TimeoutError

    future = NoReply()

    def schedule(pending, loop):
        assert loop is owner
        queued.append(pending)
        if failure == "closed":
            raise RuntimeError("Event loop is closed")
        pending.close()  # The double retains no actual owner task.
        return future

    with monkeypatch.context() as patch:
        harness.service._event_loop = owner
        patch.setattr(owner, "is_running", lambda: True)
        patch.setattr(asyncio, "run_coroutine_threadsafe", schedule)
        with pytest.raises(UpdateConflict, match="Check update status before retrying"):
            harness.service.cancel("absent")
        assert inspect.getcoroutinestate(queued[0]) == inspect.CORO_CLOSED
        assert future.cancelled() == (failure == "stalled")
        assert harness.store.read() is None and not harness.launched
        harness.service._event_loop = None
    owner.close()


def test_cancel_invalidates_preparation_before_late_safe_point(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canceled owned stop may finish later, but it must never snapshot or install."""
    async def run() -> None:
        entered, finish = asyncio.Event(), asyncio.Event()

        async def owned_stop() -> bool:
            await finish.wait()
            return True

        owned = asyncio.create_task(owned_stop())

        async def pending(mode: str) -> bool:
            harness.system.workspace_admission.close_starts()
            entered.set()
            return await asyncio.shield(owned)

        monkeypatch.setattr(harness.system, "prepare_update", pending)
        attempt = harness.service.install(SHA_B, "now", confirm_activity=True)
        advance = asyncio.create_task(harness.service.advance())
        await entered.wait()
        harness.service.cancel(attempt.attempt_id)
        assert harness.store.read() is None
        assert not harness.system.workspace_admission.starts_closed
        assert not harness.system.workspace_admission.closed
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await advance
        assert await owned
        assert not harness.launched
        assert harness.store.read() is None

    asyncio.run(run())


def test_stale_orphan_recovery_cannot_release_a_new_retry(harness: Harness) -> None:
    """Old file cleanup and its owner callback must both preserve a newer cut."""
    barrier = UpdateBarrier(harness.root / "data/update-barrier.json")
    harness.service._update_barrier = barrier
    first = harness.service.install(SHA_B, "now", confirm_activity=True)
    harness.store.change(first.attempt_id, frozenset({"waiting"}),
                         lambda current: replace(current, state="installing"))
    barrier.freeze(first.attempt_id)
    first_generation = barrier.generation
    harness.service._paused_attempt_id = first.attempt_id
    assert harness.system.workspace_admission.claim(
        lambda: None, idle=lambda: True, require_idle=False,
    )
    released = harness.service.recover_orphaned_attempt(first.attempt_id, first_generation)
    assert released is not None and not harness.system.workspace_admission.closed
    second = harness.service.install(SHA_B, "now", confirm_activity=True)
    harness.service._paused_attempt_id = second.attempt_id
    assert harness.system.workspace_admission.claim(
        lambda: None, idle=lambda: True, require_idle=False,
    )
    barrier.freeze(second.attempt_id)
    # The old worker and owner callback resume only after the retry owns both gates.
    barrier.remove(released)
    harness.service.reopen_trial_work(first.attempt_id)
    assert harness.service.recover_orphaned_attempt(first.attempt_id, first_generation) is None
    assert barrier.path.read_text().strip() == second.attempt_id
    assert barrier.frozen and harness.system.workspace_admission.closed
    assert harness.system.workspace_admission.starts_closed
    assert harness.store.read() == second


def test_local_drain_timeout_retains_retry_after_controller_finishes(harness: Harness) -> None:
    """A live home's retained drain wait is not an abandoned startup marker."""
    barrier = UpdateBarrier(harness.root / "data/update-barrier.json")
    harness.service._update_barrier = barrier

    async def run() -> None:
        with barrier.operation() as admitted:
            assert admitted
            attempt = harness.service.install(SHA_B, "now", confirm_activity=True)
            harness.store.change(attempt.attempt_id, frozenset({"waiting"}),
                lambda current: replace(current, deadline_at=(
                    datetime.now(UTC) + timedelta(milliseconds=50)).isoformat()))
            await harness.service.advance()
            assert harness.service._advance_task is None
            assert harness.service.owns_preparation(attempt.attempt_id)
            assert harness.service.recover_orphaned_attempt(
                attempt.attempt_id, barrier.generation,
            ) is None
            status = harness.service.status()
            assert status.state == "waiting" and "result saves" in (status.detail or "")
            assert "update_now" in status.allowed_actions
            assert barrier.frozen and not harness.launched
        harness.service.update_now(attempt.attempt_id, confirm_activity=True)
        await harness.service.advance()
        current = harness.store.read()
        assert current is not None and current.state == "installing"
        assert harness.launched

    asyncio.run(run())
