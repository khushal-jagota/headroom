"""The Mac target follows the update attempt; recovery bytes outlive it; old Macs still read it.

In-process HTTP reuses the trusted ingress boundary. No browser, server, or Mac starts.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from tests.support.executor_presence import lease_request, make_current

from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
from planner.environments.app import AppManifest
from planner.environments.mac_updates import MacUpdateService
from planner.environments.runtime_contracts import RuntimeContract
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    UpdateConflict,
    import_legacy_request,
    utc_now,
)
from planner.environments.update_contracts import (
    PublishedMacUpdate,
    PublishedUpdate,
    UpdateAttempt,
    UpdateAttemptState,
)
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.app_transport import build_executor_router
from planner.executor.configuration import EXECUTOR_LEASE_PATH
from planner.executor.protocol import encode_bounded
from planner.executor.update_configuration import MAX_MAC_UPDATE_RESPONSE_BYTES
from planner.executor.update_contracts import MacUpdateReport
from planner.executor.vps_transport import VpsExecutor

RUNNING_SHA = "a" * 40
TARGET_SHA = "b" * 40
NEXT_SHA = "c" * 40
HEADERS = {"tailscale-user-login": "owner@example.test", "X-Panels-Executor-ID": "configured-mac"}


def release(app_sha: str = TARGET_SHA) -> PublishedUpdate:
    return PublishedUpdate(
        1, 2, 3,
        AppManifest(app_sha, "c" * 64, "d" * 64, RuntimeContract("3.12.12", "f" * 64)),
        "e" * 64,
        "video_upload_ledger",
        (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )


def runtime_archive(application_revision: str, executable: bytes = b"fixture executable") -> bytes:
    archive = BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, content in (
            ("mac-runtime.json", json.dumps({
                "schema_version": 1, "application_revision": application_revision,
                "platform": "darwin", "architecture": "arm64",
            }).encode()),
            ("python/bin/python3", executable),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o755
            tar.addfile(member, BytesIO(content))
    return archive.getvalue()


class MacProvider:
    """Serves one Mac runtime artifact per release, keyed by its Mac artifact id."""

    def __init__(self) -> None:
        self.archives: dict[int, bytes] = {}
        self.downloads: list[int] = []

    def publish(
        self, app_sha: str = TARGET_SHA, *, manifest_revision: str | None = None,
        executable: bytes = b"fixture executable",
    ) -> PublishedUpdate:
        archive = runtime_archive(manifest_revision or app_sha, executable)
        artifact_id = len(self.archives) + 4
        self.archives[artifact_id] = archive
        mac = PublishedMacUpdate(artifact_id, hashlib.sha256(archive).hexdigest(), len(archive))
        return replace(release(app_sha), mac=mac)

    def available(self) -> PublishedUpdate | None:
        return None

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        self.downloads.append(artifact_id)
        with zipfile.ZipFile(destination, "w") as artifact:
            artifact.writestr("panels-mac-runtime.tar.gz", self.archives[artifact_id])
            artifact.writestr("mac-runtime-publication.json", "{}")


def attempt(
    published: PublishedUpdate,
    state: UpdateAttemptState,
    *,
    mode: Literal["now", "idle"] = "now",
    mac_attempt_id: str | None = "mac_one",
) -> UpdateAttempt:
    now = utc_now()
    return UpdateAttempt(
        attempt_id=uuid4().hex, from_sha=RUNNING_SHA, to_sha=published.manifest.app_sha,
        mode=mode, confirm_activity=True, state=state, created_at=now, updated_at=now,
        release=published, mac_attempt_id=mac_attempt_id,
    )


def mac_service(
    tmp_path: Path,
    provider: MacProvider | None,
    current_app_sha: str = RUNNING_SHA,
    ready: PublishedUpdate | None = None,
) -> MacUpdateService:
    return MacUpdateService(
        tmp_path / "mac-updates",
        FileUpdateAttemptStore(tmp_path / "update-attempt.json"),
        provider,
        current_app_sha,
        lambda: ready,
    )


def write(service: MacUpdateService, value: UpdateAttempt) -> None:
    """Put this attempt in the record, whatever it held before."""
    service.attempts.path.unlink(missing_ok=True)
    service.attempts.create(value)


def executor_app(service: MacUpdateService) -> FastAPI:
    app = FastAPI()
    app.state.executor_id = "configured-mac"
    app.state.mac_updates = service
    app.include_router(build_executor_router())
    app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig("tailscale", "owner@example.test", "https://panels.example.test"),
    )
    return app


def test_target_follows_attempt_state_and_activates_only_once_the_vps_runs_it(
    tmp_path: Path,
) -> None:
    """Risk: the Mac stops before the VPS pauses, or swaps before the VPS runs the release."""
    provider = MacProvider()
    published = provider.publish()
    before = mac_service(tmp_path, provider)
    before.prepare(published)
    write(before, attempt(published, "waiting", mode="idle"))
    assert before.check().target is None
    for state in ("preparing", "installing"):
        write(before, attempt(published, state, mode="idle"))
        target = before.check().target
        assert target is not None and not target.activate
        assert (target.request_id, target.application_revision, target.mode) == (
            "mac_one", TARGET_SHA, "idle"
        )
        assert target.archive_path == "/api/executor/update/mac_one/archive"
    write(before, attempt(published, "done"))
    # A done release the VPS does not run must not hold the Mac stopped.
    assert before.check().target is None
    after = mac_service(tmp_path, None, TARGET_SHA)
    target = after.check().target
    assert target is not None and target.activate and target.request_id == "mac_one"
    assert after.archive("mac_one").read_bytes() == provider.archives[4]


def test_retry_mac_presents_a_new_target_with_no_saved_failure(tmp_path: Path) -> None:
    """Risk: a retried Mac keeps its failed report or the server accepts the stale target."""
    provider = MacProvider()
    published = provider.publish()
    service = mac_service(tmp_path, provider, TARGET_SHA)
    service.prepare(published)
    done = attempt(published, "done")
    write(service, done)
    service.report(MacUpdateReport("mac_one", TARGET_SHA, "failed", "fixture failure"), None)
    assert service.phase("mac_one") == "failed"
    service.attempts.change(
        done.attempt_id, frozenset({"done"}), lambda value: replace(value, mac_attempt_id="mac_two")
    )
    target = service.check().target
    assert target is not None and target.request_id == "mac_two" and target.activate
    assert service.phase("mac_two") is None
    status = service.status()
    assert status is not None and (status.request_id, status.phase) == ("mac_two", "waiting")
    with pytest.raises(UpdateConflict):
        service.report(MacUpdateReport("mac_one", TARGET_SHA, "waiting"), None)
    with pytest.raises(UpdateConflict):
        service.archive("mac_one")
    service.report(MacUpdateReport("mac_two", TARGET_SHA, "waiting"), None)
    assert service.phase("mac_two") == "waiting"


@pytest.mark.parametrize("damage", ["source", "digest", "size"])
def test_mac_archive_is_verified_before_it_is_pinned(tmp_path: Path, damage: str) -> None:
    """Risk: bytes that differ from the publication become a Mac target."""
    provider = MacProvider()
    published = provider.publish(manifest_revision=NEXT_SHA if damage == "source" else None)
    mac = published.mac
    assert mac is not None
    if damage == "digest":
        published = replace(published, mac=replace(mac, archive_sha256="0" * 64))
    if damage == "size":
        published = replace(published, mac=replace(mac, archive_size=mac.archive_size + 1))
    service = mac_service(tmp_path, provider)
    with pytest.raises(UpdateConflict):
        service.prepare(published)
    assert not list((tmp_path / "mac-updates/archives").glob("*.tar.gz"))


def test_last_done_target_survives_a_failed_successor_and_publication_expiry(
    tmp_path: Path,
) -> None:
    """Risk: a Mac that missed an update loses the release the VPS runs after a later failure."""
    provider = MacProvider()
    published = provider.publish()
    service = mac_service(tmp_path, provider, TARGET_SHA)
    service.prepare(published)
    write(service, attempt(published, "done"))
    successor = provider.publish(NEXT_SHA)
    # Prefetch of the next release retains the done attempt before a press replaces it.
    service.prepare(successor)
    write(service, attempt(successor, "failed", mac_attempt_id="mac_next"))
    # A new process with no provider still serves the retained, now expired, receipt.
    applied = json.loads((tmp_path / "mac-updates/applied.json").read_text())
    applied["release"]["expires_at"] = "2000-01-01T00:00:00Z"
    (tmp_path / "mac-updates/applied.json").write_text(json.dumps(applied))
    recovered = mac_service(tmp_path, None, TARGET_SHA)
    target = recovered.check().target
    assert target is not None and target.activate and target.request_id == "mac_one"
    assert recovered.archive("mac_one").read_bytes() == provider.archives[4]
    assert provider.downloads == [4, 5]


def test_mac_stopped_by_the_legacy_server_activates_after_the_bridge(tmp_path: Path) -> None:
    """Risk: a Mac the 5622d2a81 server stopped for its request stays stopped forever."""
    provider = MacProvider()
    published = provider.publish()
    # The 5622d2a81 server pinned this archive and kept a request in applied.json.
    mac_service(tmp_path, provider).prepare(published)
    (tmp_path / "mac-updates/applied.json").write_text(json.dumps({"request_id": "old"}))
    (tmp_path / "owner-update-request.json").write_text(json.dumps({
        "request_id": "legacy_request", "state": "applied", "mode": "now",
        "release": asdict(published),
    }))
    service = mac_service(tmp_path, None, TARGET_SHA)
    assert import_legacy_request(service.attempts, tmp_path, TARGET_SHA) is not None
    target = service.check().target
    assert target is not None and target.activate and target.request_id == "legacy_request"
    assert target.archive_path == "/api/executor/update/legacy_request/archive"


def test_executor_routes_work_without_work_handshake_but_reject_untrusted_callers(
    tmp_path: Path,
) -> None:
    """Risk: an untrusted caller reads runtime bytes, or a process that is away or not the
    current one confirms current. The update routes take only the ID header, as a
    protocol-21 Mac sends; "current" needs the present, leased process."""
    provider = MacProvider()
    published, ready = provider.publish(), provider.publish(NEXT_SHA)
    service = mac_service(tmp_path, provider, TARGET_SHA, ready)
    service.prepare(published)
    service.prepare(ready)
    write(service, attempt(published, "done"))
    app = executor_app(service)
    db_path = str(tmp_path / "planning.db")
    boot = connect(db_path)
    create_schema(boot)
    boot.close()
    identity = ExecutorIdentity(PROTOCOL_VERSION, TARGET_SHA, SPOOL_FORMAT_VERSION, str(uuid4()))
    # Known, but last heard from long ago: away.
    make_current(db_path, identity, at_ms=1)
    with TestClient(app) as client:
        check = client.get("/api/executor/update", headers=HEADERS).json()
        target, prefetch = check["target"], check["prefetch"]
        for path in ("/api/executor/update", target["archive_path"], prefetch["archive_path"]):
            assert client.get(path).status_code == 403
            assert (
                client.get(path, headers={**HEADERS, "X-Panels-Executor-ID": "other"}).status_code
                == 403
            )
            assert (
                client.get(
                    path, headers={**HEADERS, "Origin": "https://panels.example.test"}
                ).status_code
                == 403
            )
        assert client.get(target["archive_path"], headers=HEADERS).content == provider.archives[4]
        assert client.get(prefetch["archive_path"], headers=HEADERS).content == provider.archives[5]
        for refused in (
            "/api/executor/update/not-a-target/archive",
            f"/api/executor/update/prefetch/{target['archive_sha256']}",
        ):
            assert client.get(refused, headers=HEADERS).status_code == 409
        report = asdict(MacUpdateReport("mac_one", TARGET_SHA, "current", identity=identity))
        # No executor: no presence to confirm against.
        assert client.post("/api/executor/update", headers=HEADERS, json=report).status_code == 409
        endpoint = VpsExecutor(db_path, RealClock())
        endpoint.presence.returned = None
        app.state.executor_endpoint = endpoint
        try:
            # The process is current but away: it can neither confirm nor report its stop.
            stopping = {**report, "phase": "ready_to_restart"}
            for away in (report, stopping):
                assert (
                    client.post("/api/executor/update", headers=HEADERS, json=away).status_code
                    == 409
                )

            def lease(leased: ExecutorIdentity) -> int:
                return int(
                    client.post(
                        EXECUTOR_LEASE_PATH,
                        headers=HEADERS,
                        content=encode_bounded(lease_request(leased)),
                    ).status_code
                )

            assert lease(identity) == 200
            assert client.post("/api/executor/update", headers=HEADERS, json=stopping).json() == {
                "confirmed": False
            }
            stale = {
                **report,
                "identity": {**report["identity"], "executor_incarnation": str(uuid4())},
            }
            assert (
                client.post("/api/executor/update", headers=HEADERS, json=stale).status_code == 409
            )
            for changed in ({"phase": "invented"}, {"detail": "x" * 501}, {"request_id": "other"}):
                assert (
                    client.post(
                        "/api/executor/update", headers=HEADERS, json={**report, **changed}
                    ).status_code
                    == 409
                )
            assert client.post("/api/executor/update", headers=HEADERS, json=report).json() == {
                "confirmed": True
            }
            assert service.phase("mac_one") == "current"
            # Once replaced, the earlier process can no longer confirm.
            successor = replace(identity, executor_incarnation=str(uuid4()))
            assert lease(successor) == 200
            assert (
                client.post("/api/executor/update", headers=HEADERS, json=report).status_code == 409
            )
        finally:
            assert client.portal is not None
            client.portal.call(endpoint.close)


def test_field_mac_session_receives_the_published_archive_bytes_from_the_real_server(
    tmp_path: Path,
) -> None:
    """Risk: the server's gzip wraps the archive and a field Mac rejects the bytes it saved.

    The Mac session does not decode transport encoding but still offers gzip, as the
    53d64f0 runtime does, so this asks the way that runtime asks and checks what arrives.
    """
    import asyncio
    import random
    import socket

    import uvicorn

    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app
    from planner.executor.link import create_client_session

    # Random bytes keep the archive above the compression threshold after gzip.
    filler = random.Random(0).randbytes(8192)
    provider = MacProvider()
    published = provider.publish(executable=filler)
    ready = provider.publish(NEXT_SHA, executable=filler[::-1])
    service = mac_service(tmp_path, provider, TARGET_SHA, ready)
    service.prepare(published)
    service.prepare(ready)
    write(service, attempt(published, "done"))
    db_path = tmp_path / "planning.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(path=None, env={
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(db_path),
        "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
        "PLAN_DISPATCH_ENABLED": "0",
        "PLAN_EXECUTOR_ID": "configured-mac",
        "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
        "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "owner@example.test",
        "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "https://panels.example.test",
    })
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))
    app.state.mac_updates = service

    async def exercise() -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.01)
            origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            async with create_client_session() as session:
                async with session.get(origin + "/api/executor/update", headers=HEADERS) as check:
                    assert check.headers.get("Content-Encoding") is None
                    body = json.loads(await check.read())
                for offered in (body["target"], body["prefetch"]):
                    assert offered["archive_size"] > 1024
                    archive_url = origin + offered["archive_path"]
                    async with session.get(archive_url, headers=HEADERS) as got:
                        assert got.status == 200
                        assert got.headers.get("Content-Encoding") is None
                        received = await got.read()
                    assert hashlib.sha256(received).hexdigest() == offered["archive_sha256"]
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 5)
            listener.close()

    asyncio.run(exercise())


# The check response shape the Mac runtimes of 5622d2a81 validate, copied from that commit.
@dataclass(frozen=True, slots=True)
class FieldMacUpdateTarget:
    request_id: str
    application_revision: str
    mode: Literal["now", "idle"]
    activate: bool
    archive_sha256: str
    archive_size: int
    archive_path: str


@dataclass(frozen=True, slots=True)
class FieldMacUpdateCheck:
    schema_version: int
    target: FieldMacUpdateTarget | None


def test_mac_runtimes_in_the_field_read_the_check_response_with_prefetch(tmp_path: Path) -> None:
    """Risk: the added prefetch field breaks or bloats the response old Mac runtimes validate."""
    provider = MacProvider()
    published, ready = provider.publish(), provider.publish(NEXT_SHA)
    service = mac_service(tmp_path, provider, ready=ready)
    service.prepare(published)
    service.prepare(ready)
    write(service, attempt(published, "installing"))
    with TestClient(executor_app(service)) as client:
        body = client.get("/api/executor/update", headers=HEADERS).content
    assert len(body) <= MAX_MAC_UPDATE_RESPONSE_BYTES
    assert json.loads(body)["prefetch"]["application_revision"] == NEXT_SHA
    target = service.check().target
    assert target is not None
    seen = TypeAdapter(FieldMacUpdateCheck).validate_python(json.loads(body))
    assert seen == FieldMacUpdateCheck(1, FieldMacUpdateTarget(**asdict(target)))
