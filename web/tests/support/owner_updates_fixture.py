"""Real owner routes and durable request receipts, without repository dispatch or restart."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.contracts import ConversationSystem
from planner.environments.app import AppManifest
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
        self.store = FileUpdateRequestStore(Path(self.directory.name) / "request.json")
        self.service = UpdateService(
            self.provider,
            self.store,
            lambda: cast(ConversationSystem, self.activity),
            self.current_sha,
        )
        app = FastAPI()
        app.state.updates = self.service
        app.include_router(router, prefix="/api")

        @app.get("/api/meta")
        def meta() -> dict[str, object]:
            return {"app_sha": self.current_sha, "test_mode": True}

        self.client = TestClient(app)
        self.client.__enter__()

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.directory.cleanup()
