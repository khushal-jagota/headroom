"""Generation admission, verified copies, bounded streams, and settled cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path

import pytest

from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import TRANSFER_CHUNK_BYTES
from planner.executor.contracts import FileManifest, FileScope, FileSnapshot
from planner.executor.files import copy_verified
from planner.executor.transfers import GenerationTransfers


def _manifest(data: bytes) -> FileManifest:
    return FileManifest(
        FileScope.ticket,
        "ticket",
        "file.txt",
        hashlib.sha256(data).hexdigest(),
        len(data),
    )


def _service(root: Path, valid: Callable[[], bool] = lambda: True) -> GenerationTransfers:
    return GenerationTransfers(
        ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "executor", 1),
        root,
        valid,
    )


def test_upload_admission_is_generation_bound_and_single_use(tmp_path: Path) -> None:
    async def exercise() -> None:
        current = True
        service = _service(tmp_path / "transfers", lambda: current)
        manifest = _manifest(b"test")
        transfers = [service.create_upload(manifest) for _ in range(4)]
        assert len({item.transfer_id for item in transfers}) == 4
        assert all(
            os.stat(service.root / item.transfer_id).st_mode & 0o777 == 0o600 for item in transfers
        )
        with pytest.raises(ValueError, match="limit"):
            service.create_upload(manifest)
        first = transfers[0]
        with pytest.raises(ValueError, match="incomplete"):
            service.upload_path(first.transfer_id)

        async def chunks() -> AsyncIterator[bytes]:
            yield b"test"

        await service.upload(first.transfer_id, chunks())
        assert service.upload_path(first.transfer_id).read_bytes() == b"test"
        with pytest.raises(ValueError):
            await service.upload(first.transfer_id, chunks())
        with pytest.raises(ValueError, match="revoked"):
            service.upload_path(first.transfer_id)
        service.release(first.transfer_id)
        assert not (service.root / first.transfer_id).exists()
        current = False
        with pytest.raises(ValueError, match="revoked"):
            service.upload_path(transfers[1].transfer_id)
        with pytest.raises(ValueError, match="revoked"):
            service.create_upload(manifest)
        await service.close()
        assert not list(service.root.iterdir())

    asyncio.run(exercise())


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
            assert (service.root / transfer.transfer_id).read_bytes() == b"original"
            assert source.stat().st_ino != (service.root / transfer.transfer_id).stat().st_ino
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


@pytest.mark.parametrize("body", [b"short", b"too much data", b"modified"])
def test_bad_upload_revokes_slot(tmp_path: Path, body: bytes) -> None:
    async def exercise() -> None:
        service = _service(tmp_path / "transfers")
        slot = service.create_upload(_manifest(b"original"))

        async def chunks() -> AsyncIterator[bytes]:
            yield body

        with pytest.raises(ValueError):
            await service.upload(slot.transfer_id, chunks())
        assert slot.transfer_id not in service.transfers
        assert not (service.root / slot.transfer_id).exists()
        await service.close()

    asyncio.run(exercise())


def test_download_is_single_use_and_preserves_network_chunk_bound(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = _service(tmp_path / "transfers")
        data = b"x" * (TRANSFER_CHUNK_BYTES * 3 + 1)
        source = tmp_path / "source"
        source.write_bytes(data)
        slot = await service.create_download_async(FileSnapshot(_manifest(data), source))
        chunks = service.claim_download(slot.transfer_id)
        with pytest.raises(ValueError):
            service.claim_download(slot.transfer_id)
        blocks = [chunk async for chunk in chunks]
        assert b"".join(blocks) == data
        assert max(map(len, blocks)) == 64 * 1024
        await service.close()

    asyncio.run(exercise())
