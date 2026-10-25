"""Generation admission, verified copies, bounded streams, and settled cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from planner.executor.contracts import ExecutorIdentity, FileManifest, FileScope, FileSnapshot
from planner.executor.files import copy_verified
from planner.executor.logic.transfer import validate_transfer_path
from planner.executor.transfers import GenerationTransfers


def _manifest(data: bytes) -> FileManifest:
    return FileManifest(
        "publication",
        FileScope.ticket,
        "ticket",
        "file.txt",
        hashlib.sha256(data).hexdigest(),
        len(data),
    )


def _service(root: Path, valid: Callable[[], bool] = lambda: True) -> GenerationTransfers:
    return GenerationTransfers(ExecutorIdentity(6, "a" * 40, 2, "executor", 1), root, valid)


def test_upload_admission_is_generation_bound_and_single_use(tmp_path: Path) -> None:
    current = True
    service = _service(tmp_path / "transfers", lambda: current)
    manifest = _manifest(b"test")
    transfers = [service.create_upload(manifest) for _ in range(4)]
    assert len({item.path for item in transfers}) == 4
    assert all(os.stat(item.path).st_mode & 0o777 == 0o600 for item in transfers)
    with pytest.raises(ValueError, match="limit"):
        service.create_upload(manifest)
    first = transfers[0]
    assert service.upload_path(first.transfer_id) == Path(first.path)
    with pytest.raises(ValueError, match="revoked"):
        service.upload_path(first.transfer_id)
    service.release(first.transfer_id)
    assert not Path(first.path).exists()
    current = False
    with pytest.raises(ValueError, match="revoked"):
        service.upload_path(transfers[1].transfer_id)
    with pytest.raises(ValueError, match="revoked"):
        service.create_upload(manifest)
    asyncio.run(service.close())
    assert not list(service.root.iterdir())


def test_private_root_and_manifest_refusals(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        _service(tmp_path / "link" / "transfers")
    service = _service(tmp_path / "private")
    for manifest in (replace(_manifest(b""), byte_count=-1), replace(_manifest(b""), sha256="bad")):
        with pytest.raises(ValueError, match="manifest"):
            service.create_upload(manifest)


def test_download_copy_survives_source_handle_mutation_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        service = _service(tmp_path / "transfers")
        source = tmp_path / "source"
        source.write_bytes(b"original")
        manifest = _manifest(b"original")
        with source.open("r+b") as retained:
            transfer = await service.create_download_async(FileSnapshot(manifest, source))
            retained.write(b"modified")
            retained.flush()
            assert Path(transfer.path).read_bytes() == b"original"
            assert source.stat().st_ino != Path(transfer.path).stat().st_ino
        with pytest.raises(ValueError, match="upload revoked"):
            service.upload_path(transfer.transfer_id)
        for data in (b"short", b"too much data", b"modified"):
            source.write_bytes(data)
            with pytest.raises(ValueError):
                await service.create_download_async(FileSnapshot(manifest, source))
        assert len(service.transfers) == 1
        await service.close()
        assert not list(service.root.iterdir())

    asyncio.run(exercise())


def test_snapshot_copy_cancellation_drains_thread_before_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    finish = threading.Event()

    def paused_copy(source: Path, destination: Path, manifest: FileManifest) -> None:
        started.set()
        assert finish.wait(5)
        copy_verified(source, destination, manifest)

    monkeypatch.setattr("planner.executor.transfers.copy_verified", paused_copy)

    async def exercise() -> None:
        service = _service(tmp_path / "transfers")
        source = tmp_path / "source"
        source.write_bytes(b"test")
        transfer = asyncio.create_task(
            service.create_download_async(
                FileSnapshot(_manifest(b"test"), source),
            )
        )
        await asyncio.wait_for(asyncio.to_thread(started.wait), 5)
        transfer.cancel()
        shutdown = asyncio.create_task(service.close())
        try:
            await asyncio.sleep(0)
            assert not shutdown.done() and not transfer.done()
        finally:
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await transfer
            await shutdown
        assert not service.transfers and not list(service.root.iterdir())

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "path",
    [
        "relative",
        "/root/../escape",
        "/root//file",
        "/root/./file",
        "/root/nested/file",
        "/rooted/file",
        "/root/%2e%2e",
        "/root/a\\b",
        "/root/a\x00",
        "/root/a\n",
        "//root/file",
    ],
)
def test_transfer_path_refusals(path: str) -> None:
    with pytest.raises(ValueError):
        validate_transfer_path(path, "/root")


def test_transfer_path_accepts_home_with_spaces() -> None:
    validate_transfer_path("/home/Example User/private/random", "/home/Example User/private")
