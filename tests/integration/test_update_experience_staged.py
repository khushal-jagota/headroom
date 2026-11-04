"""File cutover -> restarted process -> real exact-SHA HTTP proof.

The existing deployment unit cases fake service and health. The browser fixture
proves real UI actions but substitutes a SHA. This one connected case protects
that gap with a disposable stdlib HTTP process. It does not claim a full packaged
Panels startup or a GitHub runner. The packaging helper and deployment transaction
are reused, including its synthetic native-runtime admission. Only this test's
recorded subprocess is stopped.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import cast

import httpx
from tests.support.staged_update_cutover import DeadlineHealth, StagedProcess
from tests.unit.test_deployment import SHA_A, SHA_B, _app
from tests.unit.test_deployment import (
    synthetic_runtime_admission as synthetic_runtime_admission,
)

from planner.conversation.contracts import ConversationSystem
from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.db import connect, create_schema
from planner.environments.app import validate_app_manifest
from planner.environments.deployment import deploy_app
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
from planner.environments.update_contracts import OwnerUpdateRequest, PublishedUpdate
from planner.environments.update_requests import FileUpdateRequestStore
from planner.environments.update_service import UpdateService


class _Activity:
    def __init__(self) -> None:
        self.workspace_admission = WorkspaceAdmission()

    def workspace_activity(self) -> str:
        return "idle"

    async def run_with_workspace_admission(self, action: Callable[[], None], **_: object) -> bool:
        action()
        return True


class _Provider:
    def __init__(self, release: PublishedUpdate) -> None:
        self.release = release

    def available(self) -> PublishedUpdate:
        return self.release

    def dispatch(self, request: OwnerUpdateRequest) -> None:
        pass

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        raise AssertionError("This proof uses the local staged artifact")


def test_staged_failure_retry_and_document_reload_serve_the_cutover_version(tmp_path: Path) -> None:
    started = time.monotonic()
    root = tmp_path / "current"
    _app(root / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    (root / "data").mkdir()
    database = root / "data/planner.db"
    with closing(connect(str(database))) as connection:
        create_schema(connection)
    release = PublishedUpdate(
        1, 2, 3, validate_app_manifest(candidate / "manifest.json", require_runtime=True),
        "e" * 64, "video_upload_ledger", (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    provider = _Provider(release)
    store = FileUpdateRequestStore(root / "data/owner-update-request.json")
    lifecycle = DeploymentLifecycleStore(root / "data/deployment-lifecycle.json")
    activity = cast(ConversationSystem, _Activity())

    def service(sha: str) -> UpdateService:
        return UpdateService(
            provider, store, lambda: activity,
            connection_factory=lambda: connect(str(database)), current_app_sha=sha,
            lifecycle_store=lifecycle,
        )

    process = StagedProcess(root)
    try:
        process.restart()
        old_document = httpx.get(process.url).text
        assert SHA_A in old_document
        owner = service(SHA_A)
        previous_id: str | None = None
        receipts: list[str] = []
        outcomes: list[str] = []
        for failure in ("unhealthy", "deadline", None):
            expected = "rolled_back" if failure is not None else "succeeded"
            request = owner.request(release, "now", confirm_activity=True)
            receipts.append(request.request_id)
            assert owner.dispatch_pending()
            lifecycle.start(
                request.request_id, SHA_B, expected_deployment_id=previous_id, prior_sha=SHA_A,
            )
            previous_id = request.request_id
            # The runner takes its real claim before the transaction stops the process.
            def claim(owner: UpdateService = owner, request_id: str = request.request_id) -> None:
                asyncio.run(owner.claim(request_id))

            def publish(
                phase: str, request_id: str = request.request_id, **values: object,
            ) -> None:
                lifecycle.transition(request_id, phase, **values)  # type: ignore[arg-type]

            snapshot = tmp_path / "snapshot.db"

            def backup(_: str, snapshot: Path = snapshot) -> Path:
                shutil.copy2(database, snapshot)
                return snapshot

            def restore(saved: Path) -> None:
                shutil.copy2(saved, database)

            result = deploy_app(
                candidate_app=candidate, current_root=root, source_db=database,
                backup=backup, restore=restore,
                service=process,
                health=DeadlineHealth(
                    process.url + "/api/health", store, expire=failure == "deadline",
                ),
                health_timeout_seconds=0.5, lifecycle_transition=publish,
                deployment_id=request.request_id, before_stop=claim,
                installation_guard=partial(store.installation_guard, request.request_id),
            )
            assert result.status == expected
            outcomes.append(result.status)
            installed = SHA_A if expected == "rolled_back" else SHA_B
            owner = service(installed)
            # No runner report: the server must settle from durable evidence.
            status = owner.status()
            assert status.request is not None
            assert status.request.state == ("failed" if installed == SHA_A else "applied")
            assert SHA_A in old_document  # The already-loaded page remains the old version.
            fresh_document = httpx.get(process.url).text
            assert installed in fresh_document
            if installed == SHA_A:
                assert "retry" in status.allowed_actions
                process.fail_sha = ""
            else:
                assert status.app_sha == SHA_B
                assert "new" in [path.name for path in (root / "app").iterdir()]
        assert len(set(receipts)) == 3
        assert not list(root.glob(".app-*"))
        print(json.dumps({
            "boundary": "real cutover + subprocess restart + exact-SHA HTTP + document reload",
            "seconds": round(time.monotonic() - started, 3), "newHosts": 1,
            "processes": len(process.pids), "outcomes": outcomes,
            "failureModes": ["unhealthy candidate", "deadline after exact-SHA health"],
            "oldDocumentSHA": SHA_A, "reloadedDocumentSHA": SHA_B,
        }))
    finally:
        process.stop()
