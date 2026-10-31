"""Transfer copies preserve bytes and settle before cancelled shutdown returns."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.files import copy_verified
from planner.executor.link_files import GenerationTransfers
from planner.executor.protocol import FileManifest, FileScope, FileSnapshot


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

    monkeypatch.setattr("planner.executor.link_files.copy_verified", paused_copy)

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
