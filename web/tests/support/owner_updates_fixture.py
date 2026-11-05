"""Real owner routes and durable request receipts, without repository dispatch or restart."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.contracts import ConversationSystem
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.core.db import connect
from planner.environments.app import AppManifest
from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
from planner.environments.runtime_contracts import RuntimeContract
from planner.environments.update_api import router
from planner.environments.update_contracts import (
    OwnerUpdateRequest,
    PublishedUpdate,
    UpdateActivity,
)
from planner.environments.update_requests import FileUpdateRequestStore
from planner.environments.update_service import UpdateService


class ActivityProbe:
    activity: UpdateActivity = "unknown"

    def workspace_activity(self) -> UpdateActivity:
        return self.activity


class FakeProvider:
    """Only repository availability and external runner dispatch are synthetic."""

    def __init__(self) -> None:
        self.offered = PublishedUpdate(
            42,
            101,
            102,
            AppManifest("b" * 40, "c" * 64, "d" * 64, RuntimeContract("3.12.12", "f" * 64)),
            "e" * 64,
            "video_upload_ledger",
            (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        )
        self.dispatched: list[OwnerUpdateRequest] = []

    def available(self) -> PublishedUpdate:
        return self.offered

    def dispatch(self, request: OwnerUpdateRequest) -> None:
        self.dispatched.append(request)

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        raise AssertionError("Browser request must not download an application")


class OwnerUpdatesFixture:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="panels-owner-updates-browser-")
        self.provider = FakeProvider()
        self.activity = ActivityProbe()
        self.current_sha = "a" * 40
        self.lifecycle = DeploymentLifecycleStore(
            Path(self.directory.name) / "deployment-lifecycle.json"
        )
        self.store = FileUpdateRequestStore(Path(self.directory.name) / "request.json")
        database_path = Path(self.directory.name) / "planner.db"
        # Cutover captures the stopped database through the current Signal schema.
        # Prepare an empty runtime, then release its ownership before that capture.
        runtime = SignalRuntimeComposition(str(database_path), 1000)
        try:
            runtime.bootstrap()
            runtime.coordinator.bind_runtime(lambda _identity: None)
            runtime.initialize()
        finally:
            runtime.close()
        self.service = UpdateService(
            self.provider,
            self.store,
            lambda: cast(ConversationSystem, self.activity),
            connection_factory=lambda: connect(str(database_path)),
            current_app_sha=self.current_sha,
            lifecycle_store=self.lifecycle,
        )
        app = FastAPI()
        app.state.updates = self.service
        self._app = app
        self.mac_status = None
        app.include_router(router, prefix="/api")

        @app.get("/api/meta")
        def meta() -> dict[str, object]:
            return {"app_sha": self.current_sha, "test_mode": True}

        self.client = TestClient(app)
        self.client.__enter__()

    @property
    def mac_status(self) -> dict[str, object] | None:
        return self._mac_status

    @mac_status.setter
    def mac_status(self, value: dict[str, object] | None) -> None:
        self._mac_status = value
        # Disabled execution has no endpoint. A configured endpoint always
        # returns the same dictionary contract as the production executor.
        self._app.state.executor_endpoint = (
            None if value is None else SimpleNamespace(health=lambda: value)
        )

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.directory.cleanup()
