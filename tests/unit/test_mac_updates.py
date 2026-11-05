"""Approve once, retain recovery bytes, and confirm only the actual target generation.

In-process HTTP reuses the trusted ingress boundary. No browser, server, or Mac starts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tarfile
import zipfile
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.unit.test_owner_updates import Provider, release

from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_api import router as updates_router
from planner.environments.update_contracts import PublishedMacUpdate
from planner.environments.update_requests import FileUpdateRequestStore, UpdateConflict
from planner.environments.update_service import UpdateService
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.app_transport import build_executor_router
from planner.executor.update_contracts import MacUpdateReport


class MacProvider(Provider):
    def __init__(self, *, manifest_revision: str | None = None) -> None:
        super().__init__()
        archive = BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            for name, content in (
                (
                    "mac-runtime.json",
                    json.dumps(
                        {
                            "schema_version": 1,
                            "application_revision": manifest_revision
                            or self.offered.manifest.app_sha,
                            "platform": "darwin",
                            "architecture": "arm64",
                        }
                    ).encode(),
                ),
                ("python/bin/python3", b"fixture executable"),
            ):
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o755
                tar.addfile(member, BytesIO(content))
        self.archive = archive.getvalue()
        self.offered = replace(
            self.offered,
            mac=PublishedMacUpdate(4, hashlib.sha256(self.archive).hexdigest(), len(self.archive)),
        )
        self.downloads: list[int] = []

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        self.downloads.append(artifact_id)
        with zipfile.ZipFile(destination, "w") as artifact:
            artifact.writestr("panels-mac-runtime.tar.gz", self.archive)
            artifact.writestr("mac-runtime-publication.json", json.dumps({
                "application_revision": self.offered.manifest.app_sha,
            }))


def services(
    tmp_path: Path,
    provider: MacProvider,
    health: dict[str, object] | None = None,
) -> tuple[UpdateService, MacUpdateService]:
    store = FileUpdateRequestStore(tmp_path / "request.json")
    mac = MacUpdateService(tmp_path / "mac", store, provider, "a" * 40)
    updates = UpdateService(
        provider,
        store,
        lambda: None,
        connection_factory=lambda: sqlite3.connect(":memory:"),
        prepare_mac_update=mac.prepare,
        mac_ready_for_dispatch=lambda request: mac.ready_for_dispatch(request, health or {}),
    )
    return updates, mac


def test_immediate_dispatch_waits_for_stop_of_the_current_connected_mac(tmp_path: Path) -> None:
    provider = MacProvider()
    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, str(uuid4()), 1)
    health: dict[str, object] = {"identity": asdict(identity), "connection_state": "ready"}
    updates, mac = services(tmp_path, provider, health)
    request = updates.request(provider.offered, "now", confirm_activity=True)
    assert not updates.dispatch_pending()
    assert not provider.dispatched
    assert mac.ready_for_dispatch(request, {"connection_state": "disconnected"})
    assert mac.ready_for_dispatch(replace(request, mode="idle"), health)
    report = MacUpdateReport(
        request.request_id, request.release.manifest.app_sha, "ready_to_restart", identity=identity
    )
    mac.report(report, health)
    identity = replace(identity, connection_generation=2)
    health["identity"] = asdict(identity)
    assert not updates.dispatch_pending()
    with pytest.raises(UpdateConflict, match="current connection"):
        mac.report(report, health)
    mac.report(replace(report, identity=identity), health)
    assert updates.dispatch_pending()
    assert provider.dispatched[0].request_id == request.request_id


@pytest.mark.parametrize("damage", ["source", "digest", "size"])
def test_mac_archive_is_verified_before_request_can_authorize_dispatch(
    tmp_path: Path,
    damage: str,
) -> None:
    provider = MacProvider(manifest_revision="c" * 40 if damage == "source" else None)
    mac_publication = provider.offered.mac
    assert mac_publication is not None
    if damage == "digest":
        provider.offered = replace(
            provider.offered, mac=replace(mac_publication, archive_sha256="0" * 64)
        )
    if damage == "size":
        provider.offered = replace(
            provider.offered,
            mac=replace(mac_publication, archive_size=len(provider.archive) + 1),
        )
    updates, mac = services(tmp_path, provider)
    with pytest.raises(UpdateConflict):
        updates.request(provider.offered, "now", confirm_activity=True)
    assert updates.store.read() is None
    assert not provider.dispatched
    assert mac.check().target is None


def test_approved_bytes_and_last_applied_target_survive_expiry_and_replaced_request(
    tmp_path: Path,
) -> None:
    provider = MacProvider()
    updates, mac = services(tmp_path, provider)
    request = updates.request(provider.offered, "now", confirm_activity=True)
    target = mac.check().target
    assert target is not None and not target.activate and target.mode == "now"
    assert mac.archive(request.request_id).read_bytes() == provider.archive
    updates.store.transition(request.request_id, frozenset({"pending"}), "applied")
    # A new VPS process sees the target release. Preserve it before replacement.
    mac = MacUpdateService(mac.root, updates.store, None, provider.offered.manifest.app_sha)
    mac.prepare(release())
    assert mac.check().target is not None
    successor = replace(request, request_id="successor", state="pending", release=release())
    updates.store.create(successor)
    unavailable = mac.status()
    assert unavailable is not None and unavailable.phase == "unavailable"
    assert mac.check().target is None
    updates.store.transition("successor", frozenset({"pending"}), "cancelled")
    # No repository/provider remains. An expired receipt still authorizes its pinned bytes.
    retained = FileUpdateRequestStore(mac.root / "applied.json")
    saved = retained.read()
    assert saved is not None
    retained.create(
        replace(saved, release=replace(saved.release, expires_at="2000-01-01T00:00:00Z"))
    )
    recovered = MacUpdateService(mac.root, updates.store, None, provider.offered.manifest.app_sha)
    target = recovered.check().target
    assert target is not None and target.activate and target.request_id == request.request_id
    assert recovered.archive(target.request_id).read_bytes() == provider.archive
    assert provider.downloads == [4]


def test_recovery_routes_work_without_work_handshake_but_reject_untrusted_callers(
    tmp_path: Path,
) -> None:
    provider = MacProvider()
    updates, mac = services(tmp_path, provider)
    request = updates.request(provider.offered, "idle", confirm_activity=False)
    app = FastAPI()
    app.state.executor_id = "configured-mac"
    app.state.mac_updates = mac
    app.state.updates = updates
    app.include_router(build_executor_router())
    app.include_router(updates_router, prefix="/api")
    app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig(
            "tailscale", "owner@example.test", "https://panels.example.test"
        ),
    )
    headers = {
        "tailscale-user-login": "owner@example.test",
        "X-Panels-Executor-ID": "configured-mac",
    }
    with TestClient(app) as client:
        for path in ("/api/executor/update", f"/api/executor/update/{request.request_id}/archive"):
            assert client.get(path).status_code == 403
            assert (
                client.get(path, headers={**headers, "X-Panels-Executor-ID": "other"}).status_code
                == 403
            )
            assert (
                client.get(
                    path, headers={**headers, "Origin": "https://panels.example.test"}
                ).status_code
                == 403
            )
        target = client.get("/api/executor/update", headers=headers).json()["target"]
        assert target["mode"] == "idle" and target["activate"] is False
        assert client.get(target["archive_path"], headers=headers).content == provider.archive
        assert (
            client.get("/api/executor/update/not-approved/archive", headers=headers).status_code
            == 409
        )
        updates.store.transition(request.request_id, frozenset({"pending"}), "applied")
        mac.current_app_sha = provider.offered.manifest.app_sha
        identity = ExecutorIdentity(
            PROTOCOL_VERSION,
            provider.offered.manifest.app_sha,
            SPOOL_FORMAT_VERSION,
            str(uuid4()),
            2,
        )
        report = MacUpdateReport(
            request.request_id, identity.application_revision, "current", identity=identity
        )
        assert (
            client.post("/api/executor/update", headers=headers, json=asdict(report)).status_code
            == 409
        )
        health: dict[str, object] = {
            "identity": asdict(identity),
            "connection_state": "ready",
            "last_contact_at_ms": 1234,
            "refusal": None,
        }
        app.state.executor_endpoint = SimpleNamespace(health=lambda: health)
        stale = replace(report, identity=replace(identity, connection_generation=1))
        assert (
            client.post("/api/executor/update", headers=headers, json=asdict(stale)).status_code
            == 409
        )
        assert client.post("/api/executor/update", headers=headers, json=asdict(report)).json() == {
            "confirmed": True
        }
        status = client.get("/api/updates").json()["mac_status"]
        assert status["connection_state"] == "ready"
        assert status["last_contact_at_ms"] == 1234
        assert status["update"]["phase"] == "current"
        assert status["update"]["target_revision"] == identity.application_revision
        for changed in ({"phase": "invented"}, {"detail": "x" * 501}, {"request_id": "other"}):
            response = client.post(
                "/api/executor/update", headers=headers, json={**asdict(report), **changed}
            )
            assert response.status_code == 409
