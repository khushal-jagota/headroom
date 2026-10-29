"""Changed update risks: identity, durable intent, admission races, and commit ordering.

Reuse existing conversation/deployment fakes. No new host or browser is necessary.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.unit.test_conversation_system import _Harness, _run, _start, harness  # noqa: F401, F811
from tests.unit.test_deployment import SHA_A, SHA_B, FakeHealth, FakeService, _app

from planner.conversation.contracts import ConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core.errors import PlannerError
from planner.core.server import handle_planner_error
from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
from planner.environments.app import AppManifest
from planner.environments.deployment import deploy_app
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
from planner.environments.runtime_contracts import RuntimeContract
from planner.environments.update_admission import matching_commit, startup_requires_commit
from planner.environments.update_api import router
from planner.environments.update_contracts import OwnerUpdateRequest, PublishedUpdate
from planner.environments.update_requests import FileUpdateRequestStore, UpdateConflict
from planner.environments.update_service import UpdateService


def release() -> PublishedUpdate:
    return PublishedUpdate(
        1,
        2,
        3,
        AppManifest(SHA_B, "c" * 64, "d" * 64, RuntimeContract("3.12.12", "f" * 64)),
        "e" * 64,
        "video_upload_ledger",
        (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )


class Provider:
    def __init__(self) -> None:
        self.reads = 0
        self.offered = release()
        self.dispatched: list[OwnerUpdateRequest] = []

    def available(self) -> PublishedUpdate:
        self.reads += 1
        return self.offered

    def dispatch(self, request: OwnerUpdateRequest) -> None:
        self.dispatched.append(request)

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        raise AssertionError("request handling must not download an application")


def test_named_agents_remain_refused_through_hosted_ingress(tmp_path: Path) -> None:
    app = FastAPI()
    app.add_exception_handler(PlannerError, handle_planner_error)
    app.include_router(router, prefix="/api")
    app.state.updates = UpdateService(
        Provider(), FileUpdateRequestStore(tmp_path / "request.json"), lambda: None
    )
    app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig(
            "tailscale", "owner@example.test", "https://panels.example.test"
        ),
    )
    with TestClient(app) as client:
        for actor, identity in (
            ("chief", {}),
            ("manager", {"X-Plan-Manager-ID": "m"}),
            ("worker", {"X-Plan-Ticket-ID": "t"}),
        ):
            response = client.post(
                "/api/updates/requests/x/claim",
                headers={
                    "tailscale-user-login": "owner@example.test",
                    "X-Plan-Actor": actor,
                    **identity,
                },
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "agent_forbidden"
        assert client.get("/api/updates").json()["configured"] is True


def test_pinned_request_survives_new_publication_and_cannot_be_claimed_after_cancel(
    tmp_path: Path,
    harness: _Harness,  # noqa: F811
) -> None:
    provider = Provider()
    store = FileUpdateRequestStore(tmp_path / "request.json")
    service = UpdateService(provider, store, lambda: cast(ConversationSystem, harness.system))
    request = service.request(provider.offered, "idle", confirm_activity=False)
    provider.offered = replace(provider.offered, artifact_id=99)
    assert service.dispatch_pending()
    assert provider.dispatched[0].release == request.release
    service.cancel(request.request_id)
    with pytest.raises(UpdateConflict, match="cancelled"):
        asyncio.run(service.claim(request.request_id))
    assert not harness.system.workspace_admission.closed
    expired = replace(provider.offered, expires_at="2020-01-01T00:00:00Z")
    with pytest.raises(UpdateConflict, match="expired"):
        service.request(expired, "idle", confirm_activity=False)


def test_idle_claim_rechecks_new_send_and_now_claim_does_not_wait_for_backend(
    tmp_path: Path,
    harness: _Harness,  # noqa: F811
) -> None:
    async def exercise() -> None:
        await _start(harness, "first")
        await _start(harness, "second")
        provider = Provider()
        store = FileUpdateRequestStore(tmp_path / "request.json")
        service = UpdateService(provider, store, lambda: cast(ConversationSystem, harness.system))
        request = service.request(provider.offered, "idle", confirm_activity=False)
        assert service.dispatch_pending()
        first = harness.backend("first")
        first.write_has_begun = asyncio.Event()
        first.writes_wait_for_release = asyncio.Event()
        sending = asyncio.create_task(
            harness.system.send("first", text_message_content("slow"), sender_label="Owner")
        )
        await first.write_has_begun.wait()
        # Another conversation still reaches its backend while the first send waits.
        await asyncio.wait_for(
            harness.system.send(
                "second", text_message_content("independent"), sender_label="Owner"
            ),
            1,
        )
        with pytest.raises(UpdateConflict, match="activity changed"):
            await service.claim(request.request_id)
        assert store.read().state == "pending"  # type: ignore[union-attr]
        service.cancel(request.request_id)
        request = service.request(provider.offered, "now", confirm_activity=True)
        assert service.dispatch_pending()
        assert (await asyncio.wait_for(service.claim(request.request_id), 1)).state == "applying"
        with pytest.raises(UpdateConflict, match="already began"):
            service.cancel(request.request_id)
        with pytest.raises(RuntimeError, match="admission is closed"):
            await _start(harness, "too-late")
        first.writes_wait_for_release.set()
        await sending

    _run(exercise)


def test_health_commit_publication_error_never_restores_database(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old", include_cli=False)
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = current / "data" / "planner.db"
    database.parent.mkdir()
    database.write_text("db")
    events: list[str] = []
    store = DeploymentLifecycleStore(current / "data" / "deployment-lifecycle.json")
    store.start("test", SHA_B, prior_sha=SHA_A)

    def publish(phase: str, **kwargs: object) -> None:
        store.transition("test", phase, **kwargs)  # type: ignore[arg-type]
        if phase == "app_healthy":
            raise OSError("directory fsync failed after replace")

    with pytest.raises(OSError, match="fsync"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: events.append("restore"),
            service=FakeService(events),
            health=FakeHealth(events, {SHA_B}),
            lifecycle_transition=publish,
        )
    assert matching_commit(store, SHA_B)
    assert not startup_requires_commit(store, SHA_B)
    assert "restore" not in events
    assert (current / "app" / "new").is_file()


def test_now_request_rechecks_confirmation_and_hides_installed_release(
    tmp_path: Path,
    harness: _Harness,  # noqa: F811
) -> None:
    async def exercise() -> None:
        provider = Provider()
        store = FileUpdateRequestStore(tmp_path / "request.json")
        service = UpdateService(provider, store, lambda: cast(ConversationSystem, harness.system))
        request = service.request(provider.offered, "now", confirm_activity=False)
        assert service.dispatch_pending()
        await _start(harness, "new-work")
        await harness.system.send("new-work", text_message_content("active"), sender_label="Owner")
        with pytest.raises(UpdateConflict, match="confirmation"):
            await service.claim(request.request_id)
        receipt = store.read()
        assert receipt is not None and receipt.state == "failed"
        assert not harness.system.workspace_admission.closed
        installed = UpdateService(provider, store, lambda: None, current_app_sha=SHA_B)
        assert installed.status().available is None
        with pytest.raises(UpdateConflict, match="already installed"):
            installed.request(provider.offered, "now", confirm_activity=True)

    _run(exercise)


def test_background_admission_is_part_of_atomic_idle_claim(harness: _Harness) -> None:  # noqa: F811
    async def exercise() -> None:
        written: list[str] = []
        with harness.system.workspace_admission.operation() as admitted:
            assert admitted
            assert harness.system.workspace_activity() == "busy"
            assert not await harness.system.run_with_workspace_admission(
                lambda: written.append("claimed"), require_idle=True
            )
            assert await harness.system.run_with_workspace_admission(
                lambda: written.append("claimed"), require_idle=False
            )
        with harness.system.workspace_admission.operation() as admitted:
            assert not admitted
        assert written == ["claimed"]

    _run(exercise)


def test_candidate_holds_normal_http_and_cleanup_until_matching_runner_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.conversation.video_uploads.service import VideoUploadService
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect, create_schema
    from planner.core.server import create_app

    database = tmp_path / "data" / "planner.db"
    database.parent.mkdir()
    conn = connect(str(database))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(database),
            "PLAN_APP_SHA": SHA_B,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "polling.lock"),
        },
    )
    request_store = FileUpdateRequestStore(database.parent / "owner-update-request.json")
    request_store.create(
        OwnerUpdateRequest("request", release(), "now", "applying", datetime.now(UTC).isoformat())
    )
    progress = DeploymentLifecycleStore(database.parent / "deployment-lifecycle.json")
    progress.path.write_text("unreadable progress")
    old_epoch = database.parent / "conversation-view-output" / "epoch-prior"
    old_epoch.mkdir(parents=True)
    (old_epoch / "retained.json").write_text("prior output")
    cleaned: list[bool] = []
    monkeypatch.setattr(VideoUploadService, "recover_and_cleanup", lambda _: cleaned.append(True))
    app = create_app(config, build_clock(config), lambda: connect(str(database)))
    with TestClient(app) as client:
        assert client.get("/api/health", params={"expected_sha": SHA_B}).status_code == 200
        assert client.get("/api/worker-types").status_code == 503
        assert app.state.conversation is None
        assert cleaned == []
        assert (old_epoch / "retained.json").read_text() == "prior output"
        assert "Recovery required" in client.get("/api/deployment-status").json()["detail"]
        progress.start("runner", SHA_B, prior_sha=SHA_A)
        progress.transition("runner", "verifying")
        assert client.get("/api/worker-types").status_code == 503
        progress.transition("runner", "app_healthy", serving_sha=SHA_B)
        assert client.portal is not None
        client.portal.call(asyncio.sleep, 0.5)
        assert client.get("/api/worker-types").status_code == 200
        assert cleaned == [True]
        assert not old_epoch.exists()
        stale = client.post("/api/projects", headers={"X-Panels-App-SHA": SHA_A}, json={})
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "validation"
        assert stale.json()["error"]["message"] == "Refresh page before making changes."


def test_uncertain_dispatch_timeout_records_failure_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from planner.environments.update_provider import GitHubUpdateProvider

    provider = GitHubUpdateProvider("owner/repository", None, runner_token="runner-only")
    attempts: list[str] = []

    def timeout(_client: httpx.Client, method: str, url: str, **kwargs: object) -> httpx.Response:
        attempts.append(method)
        raise httpx.ReadTimeout("uncertain accepted dispatch")

    monkeypatch.setattr(httpx.Client, "request", timeout)
    store = FileUpdateRequestStore(tmp_path / "request.json")
    # Update now with explicit confirmation is valid when activity is unknown.
    request = UpdateService(provider, store, lambda: None).request(
        release(), "now", confirm_activity=True
    )
    service = UpdateService(provider, store, lambda: None)
    assert not service.dispatch_pending()
    receipt = store.read()
    assert receipt is not None and receipt.request_id == request.request_id
    assert receipt.state == "failed" and "uncertain" in (receipt.detail or "")
    assert not service.dispatch_pending()
    assert attempts == ["GET", "POST"]
    assert "unavailable" in (service.status().detail or "")


def test_readiness_discovers_new_offer_once_per_cadence_and_persists_receipt(
    tmp_path: Path,
) -> None:
    from planner.core import change_signal
    from planner.environments.update_configuration import UPDATE_AVAILABILITY_REFRESH_SECONDS

    now = [1000.0]
    provider = Provider()
    store = FileUpdateRequestStore(tmp_path / "request.json")
    availability = tmp_path / "availability.json"
    service = UpdateService(
        provider, store, lambda: None, availability_path=availability, now=lambda: now[0]
    )
    signals: list[bool] = []
    unsubscribe = change_signal.subscribe(lambda: signals.append(True))
    try:
        for _ in range(5):
            assert not service.dispatch_pending()
        assert provider.reads == 1 and len(signals) == 1
        old = service.status().available
        provider.offered = replace(provider.offered, artifact_id=55)
        now[0] += UPDATE_AVAILABILITY_REFRESH_SECONDS
        assert not service.dispatch_pending()
        assert provider.reads == 2 and len(signals) == 2
        assert service.status().available != old
        reloaded = UpdateService(
            provider, store, lambda: None, availability_path=availability, now=lambda: now[0]
        )
        assert reloaded.status().available == provider.offered
        assert provider.reads == 2
    finally:
        unsubscribe()


def test_runner_refuses_unknown_schema_before_any_shutdown(tmp_path: Path) -> None:
    import sqlite3

    from planner.environments.update_runner import validate_schema_ancestry

    database = tmp_path / "schema.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('video_upload_ledger')")
    validate_schema_ancestry(database)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE alembic_version SET version_num = 'unknown_migration'")
    with pytest.raises(ValueError, match="unknown schema ancestry"):
        validate_schema_ancestry(database)


def test_provider_rechecks_metadata_without_repeated_proof_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import zipfile
    from dataclasses import asdict
    from io import BytesIO
    from typing import Any

    import httpx

    from planner.environments.update_provider import GitHubUpdateProvider

    provider = GitHubUpdateProvider("owner/repository", None, runner_token="runner-only")
    offered = release()
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr("publication.json", json.dumps(asdict(offered)))
    metadata_reads: list[str] = []
    proof_reads: list[str] = []
    expired = [False]

    def respond(_client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
        path = url.removeprefix("https://api.github.com/repos/owner/repository/")
        if path == "actions/workflows/deploy.yml/runs":
            return httpx.Response(200, json={"workflow_runs": [{"id": 1}]})
        if path == "actions/runs/1":
            metadata_reads.append("run")
            return httpx.Response(
                200,
                json={
                    "head_branch": "main",
                    "event": "push",
                    "conclusion": "success",
                    "head_repository": {"full_name": "owner/repository"},
                    "path": ".github/workflows/deploy.yml",
                    "head_sha": SHA_B,
                },
            )
        if path == "actions/runs/1/artifacts":
            metadata_reads.append("artifacts")
            return httpx.Response(
                200,
                json={
                    "artifacts": [
                        {"id": 2, "name": "panels-app", "expired": expired[0]},
                        {"id": 3, "name": "panels-publication-proof", "expired": False},
                    ]
                },
            )
        assert path == "actions/artifacts/3/zip"
        proof_reads.append(path)
        return httpx.Response(200, content=payload.getvalue())

    monkeypatch.setattr(httpx.Client, "request", respond)
    assert provider.available() == offered
    assert provider.available() == offered
    expired[0] = True
    assert provider.available() is None
    assert len(proof_reads) == 1
    assert metadata_reads == ["run", "artifacts"] * 3


@pytest.mark.parametrize("state", ["dispatched", "cancelled", "applying"])
def test_runner_preflight_failure_preserves_cancellation_and_uncertain_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    from argparse import Namespace
    from typing import cast

    from planner.environments import update_runner
    from planner.environments.update_contracts import UpdateRequestState

    store = FileUpdateRequestStore(tmp_path / "data" / "owner-update-request.json")
    store.create(
        OwnerUpdateRequest("request", release(), "now", cast(UpdateRequestState, state), "now")
    )
    arguments = [
        "runner",
        "apply",
        "--current-root",
        str(tmp_path),
        "--request-id",
        "request",
        "--app-sha",
        SHA_B,
        "--artifact-digest",
        "d" * 64,
        "--archive-sha256",
        "e" * 64,
        "--artifact-id",
        "2",
        "--proof-artifact-id",
        "3",
        "--publication-run-id",
        "1",
    ]
    monkeypatch.setattr("sys.argv", arguments)

    def refuse(_args: Namespace) -> dict[str, object]:
        raise ValueError("expired, missing artifact, or unknown ancestry")

    monkeypatch.setattr(update_runner, "apply", refuse)
    assert update_runner.main() == 1
    receipt = store.read()
    assert receipt is not None
    if state == "dispatched":
        assert receipt.state == "failed"
    else:
        assert receipt.state == state
    if state == "applying":
        assert "Recovery required" in (receipt.detail or "")


def test_extracted_older_candidate_rejects_live_schema_before_claim_or_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import shutil
    import sqlite3
    import zipfile
    from argparse import Namespace
    from dataclasses import asdict
    from typing import Any

    from alembic.script import ScriptDirectory

    from planner.core.db import MIGRATIONS_DIRECTORY
    from planner.environments import update_runner
    from planner.environments.app import (
        digest_app_artifact,
        digest_app_source,
        validate_app_manifest,
    )
    from planner.environments.update_artifacts import create_app_archive

    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old", include_cli=False)
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    history = candidate / "src" / "planner" / "core" / "migrations"
    (history / "versions").mkdir(parents=True)
    shutil.copy(MIGRATIONS_DIRECTORY / "env.py", history / "env.py")
    # Copy only the predecessor chain, regardless of later shipped descendants.
    for revision in ScriptDirectory(str(MIGRATIONS_DIRECTORY)).walk_revisions(
        base="base", head="owner_message_preview"
    ):
        shutil.copy(revision.path, history / "versions" / Path(revision.path).name)
    manifest_path = candidate / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_digest"] = digest_app_source(candidate)
    manifest["artifact_digest"] = digest_app_artifact(candidate)
    manifest_path.write_text(json.dumps(manifest))
    sealed = tmp_path / "panels-app.tar.gz"
    checksum = create_app_archive(candidate, sealed)
    offered = replace(
        release(), manifest=validate_app_manifest(manifest_path), archive_sha256=checksum
    )
    database = current / "data" / "planner.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('video_upload_ledger')")
    update_runner.validate_schema_ancestry(database)
    store = FileUpdateRequestStore(database.parent / "owner-update-request.json")
    store.create(OwnerUpdateRequest("request", offered, "idle", "dispatched", "now"))
    effects: list[str] = []

    class RetainedProvider:
        def publication(self, run_id: int) -> PublishedUpdate:
            return offered

        def download_artifact(self, artifact_id: int, destination: Path) -> None:
            with zipfile.ZipFile(destination, "w") as zipped:
                if artifact_id == offered.proof_artifact_id:
                    zipped.writestr("publication.json", json.dumps(asdict(offered)))
                else:
                    zipped.write(sealed, "panels-app.tar.gz")

    monkeypatch.setattr(
        update_runner, "GitHubUpdateProvider", lambda *args, **kwargs: RetainedProvider()
    )
    monkeypatch.setattr(
        update_runner, "SubprocessServiceController", lambda *args: FakeService(effects)
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repository")

    def claim(*args: Any, **kwargs: Any) -> None:
        effects.append("claim")
        raise AssertionError("unsupported candidate must not claim")

    monkeypatch.setattr("planner.environments.update_runner.httpx.post", claim)
    arguments = Namespace(
        current_root=current,
        request_id="request",
        publication_run_id=1,
        artifact_id=2,
        proof_artifact_id=3,
        app_sha=SHA_B,
        artifact_digest=offered.manifest.artifact_digest,
        archive_sha256=checksum,
        health_url="http://127.0.0.1:1/api/health",
    )
    with pytest.raises(ValueError, match="unknown schema ancestry"):
        update_runner.apply(arguments)
    assert effects == []
    assert store.read().state == "dispatched"  # type: ignore[union-attr]


@pytest.fixture(autouse=True)
def synthetic_runtime_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    # Transaction tests use shell stubs. Real admission has separate focused tests.
    monkeypatch.setattr("planner.environments.deployment.admit_runtime", lambda *args: None)
