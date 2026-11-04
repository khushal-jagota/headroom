"""Distinct risks: quota races, actual byte bounds, and expiry racing admission."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from planner.conversation.video_uploads import config
from planner.conversation.video_uploads import service as service_module
from planner.conversation.video_uploads.api import create_router
from planner.conversation.video_uploads.contracts import (
    ReserveVideoUpload,
    UploadDestination,
    VideoUploadError,
)
from planner.conversation.video_uploads.service import VideoUploadService
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError


@pytest.fixture
def intake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VideoUploadService:
    db = tmp_path / "plan.db"
    conn = connect(str(db))
    create_schema(conn)
    conn.close()
    monkeypatch.setattr(service_module, "require_probe", lambda: "ffprobe")
    monkeypatch.setattr(config, "FREE_SPACE_FLOOR_BYTES", 0)
    return VideoUploadService(db)


def reservation(size: int = 8) -> ReserveVideoUpload:
    return ReserveVideoUpload(
        destination=UploadDestination(kind="ticket", id="t_test", expected_conversation_id=None),
        filename="clip.mp4",
        byte_count=size,
    )


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def accept_probe(path: Path, filename: str) -> str:
    return "video/mp4"


def test_concurrent_reservations_do_not_double_spend(
    intake: VideoUploadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "VIDEO_BUDGET_BYTES", 10)

    def reserve() -> str | int:
        try:
            return intake.reserve(reservation()).state
        except VideoUploadError as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(map(str, results)) == ["507", "reserved"]
    # Reservation covers stored temporary bytes exactly once.
    monkeypatch.setattr(service_module, "probe_video", accept_probe)
    with intake._transaction() as conn:
        upload_id = conn.execute("SELECT upload_id FROM conversation_video_uploads").fetchone()[0]
    asyncio.run(intake.transfer(upload_id, chunks(b"1234", b"5678")))
    assert intake.reserve(reservation(2)).state == "reserved"


def test_transfer_bounds_and_failure_release_reservation(
    intake: VideoUploadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "VIDEO_BUDGET_BYTES", 8)
    first = intake.reserve(reservation())
    with pytest.raises(VideoUploadError, match="exceeds"):
        asyncio.run(intake.transfer(first.upload_id, chunks(b"123456789")))
    assert intake.status(first.upload_id).state == "failed"
    assert not intake._temporary(first.upload_id).exists()
    second = intake.reserve(reservation())
    with pytest.raises(VideoUploadError, match="interrupted"):
        asyncio.run(intake.transfer(second.upload_id, chunks(b"1234")))
    assert intake.reserve(reservation()).state == "reserved"


def test_first_send_binding_retry_pin_and_atomic_recovery(
    intake: VideoUploadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "probe_video", accept_probe)
    request = reservation()
    upload = intake.reserve(request)
    asyncio.run(intake.transfer(upload.upload_id, chunks(b"12345678")))
    changed = request.destination.model_copy(update={"expected_conversation_id": "c_other"})
    with pytest.raises(VideoUploadError, match="changed"):
        intake.preflight(upload.upload_id, changed, "message_1")
    checked = intake.preflight(upload.upload_id, request.destination, "message_1")
    # Crash after recording target but before atomic rename can be retried.
    with intake._transaction() as conn:
        conn.execute(
            "UPDATE conversation_video_uploads SET conversation_id=?,expires_at=0 "
            "WHERE upload_id=?",
            ("c_first", upload.upload_id),
        )
    intake.recover_and_cleanup()
    assert intake.status(upload.upload_id).state == "admitted"
    retry = intake.preflight(upload.upload_id, changed, "message_1")
    assert retry.conversation_id == "c_first"
    kept = intake.adopt(retry, "c_first")
    assert kept.absolute_path.read_bytes() == b"12345678"
    assert not intake._temporary(upload.upload_id).exists()
    intake.finish(checked, "accepted")
    intake.finish(checked, "refused")
    with pytest.raises(VideoUploadError, match="in use"):
        intake.cancel(upload.upload_id)
    intake.recover_and_cleanup()
    assert kept.absolute_path.exists()


def test_expiry_and_cancel_release_capacity_but_not_admission(
    intake: VideoUploadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "probe_video", accept_probe)
    upload = intake.reserve(reservation())
    with intake._transaction() as conn:
        conn.execute("UPDATE conversation_video_uploads SET expires_at=0")
    intake.recover_and_cleanup()
    assert intake.status(upload.upload_id).state == "cancelled"
    upload = intake.reserve(reservation())
    asyncio.run(intake.transfer(upload.upload_id, chunks(b"12345678")))
    checked = intake.preflight(upload.upload_id, reservation().destination, "msg")
    intake.finish(checked, "refused")
    intake.cancel(upload.upload_id)
    assert intake.status(upload.upload_id).state == "cancelled"


def test_owner_only_raw_upload_http_boundary(
    intake: VideoUploadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "probe_video", accept_probe)
    app = FastAPI()

    async def authorize(destination: UploadDestination) -> None:
        assert destination.id == "t_test"

    app.include_router(create_router(intake, authorize))

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with pytest.raises(PlannerError, match="only to the owner"):
                await client.post(
                    "/api/conversation/video-uploads",
                    json=reservation().model_dump(),
                    headers={"X-Plan-Actor": "chief"},
                )
            response = await client.post(
                "/api/conversation/video-uploads", json=reservation().model_dump()
            )
            assert response.status_code == 200
            upload_id = response.json()["upload_id"]
            response = await client.put(
                f"/api/conversation/video-uploads/{upload_id}/bytes", content=b"12345678"
            )
            assert response.status_code == 200
            assert response.json()["state"] == "ready"
            response = await client.delete(f"/api/conversation/video-uploads/{upload_id}")
            assert response.status_code == 204

    asyncio.run(exercise())


def test_definite_refusal_first_send_can_adopt_recreated_conversation(
    intake: VideoUploadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "probe_video", accept_probe)
    request = reservation()
    upload = intake.reserve(request)
    asyncio.run(intake.transfer(upload.upload_id, chunks(b"12345678")))
    checked = intake.preflight(upload.upload_id, request.destination, "msg")
    old = intake.adopt(checked, "c_removed")
    intake.finish(checked, "refused")
    assert not old.absolute_path.exists()
    retry = intake.preflight(upload.upload_id, request.destination, "msg")
    assert retry.conversation_id is None
    new = intake.adopt(retry, "c_recreated")
    assert new.absolute_path.read_bytes() == b"12345678"


def test_expired_active_transfer_cannot_publish_ready(
    intake: VideoUploadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "probe_video", accept_probe)
    upload = intake.reserve(reservation())

    async def interrupted() -> AsyncIterator[bytes]:
        yield b"1234"
        with intake._transaction() as conn:
            conn.execute("UPDATE conversation_video_uploads SET expires_at=0")
        intake.recover_and_cleanup()
        yield b"5678"

    with pytest.raises(VideoUploadError, match="expired"):
        asyncio.run(intake.transfer(upload.upload_id, interrupted()))
    assert intake.status(upload.upload_id).state != "ready"
    assert not intake._temporary(upload.upload_id).exists()


def test_real_probe_distinguishes_mp4_mov_webm_and_renamed_matroska(tmp_path: Path) -> None:
    """Real demuxing is the boundary mocks cannot prove; no application server needed."""
    import shutil
    import subprocess

    from planner.conversation.video_uploads.probe import probe_video

    assert shutil.which("ffprobe"), "ffprobe runtime prerequisite is missing"
    assert shutil.which("ffmpeg"), "ffmpeg is needed to generate tiny test fixtures"
    for suffix, codec, media_type in (
        ("mp4", "mpeg4", "video/mp4"),
        ("mov", "mpeg4", "video/quicktime"),
        ("webm", "libvpx", "video/webm"),
        ("mkv", "libvpx", None),
    ):
        target = tmp_path / f"clip.{suffix}"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=size=16x16:rate=1",
                "-frames:v",
                "1",
                "-c:v",
                codec,
                str(target),
            ],
            check=True,
            timeout=10,
        )
        if media_type is None:
            with pytest.raises(VideoUploadError, match="container"):
                asyncio.run(probe_video(target, "renamed.webm"))
        else:
            assert asyncio.run(probe_video(target, target.name)) == media_type


def test_precreation_crash_keeps_explicit_absence_binding(
    intake: VideoUploadService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Existing recovery coverage starts after a target exists. This case protects the
    # earlier admission-to-adoption gap, without a server or another connected host.
    monkeypatch.setattr(service_module, "probe_video", accept_probe)
    request = reservation()
    upload = intake.reserve(request)
    asyncio.run(intake.transfer(upload.upload_id, chunks(b"12345678")))
    intake.preflight(upload.upload_id, request.destination, "message_1")
    restarted = VideoUploadService(intake.db_path)
    restarted.recover_and_cleanup()
    changed = request.destination.model_copy(update={"expected_conversation_id": "c_competitor"})
    with pytest.raises(VideoUploadError, match="changed"):
        restarted.preflight(upload.upload_id, changed, "message_1")
    retry = restarted.preflight(upload.upload_id, request.destination, "message_1")
    assert retry.conversation_id is None
    assert restarted.status(upload.upload_id).state == "admitted"
