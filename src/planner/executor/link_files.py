"""Generation-owned file reservations over bounded HTTP bodies."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from planner.core.task_settlement import wait_for_settlement
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import FILE_CHUNK_BYTES, MAX_TRANSFERS, TRANSFER_CHUNK_BYTES
from planner.executor.files import copy_verified, directory_descriptor, regular_descriptor
from planner.executor.protocol import FileManifest, FileSnapshot, FileTransfer


async def settled_io[Result](function: Callable[..., Result], *args: Any) -> Result:
    """Keep ownership until a complete blocking I/O unit settles, even on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except BaseException:
        await wait_for_settlement(task)
        raise


@dataclass
class _Transfer:
    contract: FileTransfer
    started: bool = False
    completed: bool = False
    consumed: bool = False
    readers: int = 0
    release_requested: bool = False


class GenerationTransfers:
    def __init__(self, identity: ExecutorIdentity, root: Path, valid: Callable[[], bool]) -> None:
        self.identity, self.root, self._valid = identity, root, valid
        self.closed = False
        self.transfers: dict[str, _Transfer] = {}
        self.preparations: set[asyncio.Task[Any]] = set()
        self.active: set[asyncio.Task[Any]] = set()
        if not root.is_absolute():
            raise ValueError("transfer directory must be absolute")
        with directory_descriptor(root, create=True) as descriptor:
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("transfer directory must be private and owned")

    def valid(self) -> bool:
        return not self.closed and self._valid()

    def _admit(self, manifest: FileManifest) -> None:
        if not self.valid():
            raise ValueError("generation revoked")
        if len(self.transfers) >= MAX_TRANSFERS:
            raise ValueError("transfer limit")
        if manifest.byte_count < 0 or not re.fullmatch("[a-f0-9]{64}", manifest.sha256):
            raise ValueError("invalid manifest length or digest")

    def _claim(self, transfer_id: str, direction: str) -> _Transfer:
        transfer = self.transfers.get(transfer_id)
        if (
            not self.valid()
            or transfer is None
            or transfer.started
            or transfer.contract.direction != direction
        ):
            raise ValueError("unknown, consumed, or stale transfer")
        transfer.started = True
        return transfer

    def create_upload(self, manifest: FileManifest) -> FileTransfer:
        self._admit(manifest)
        transfer_id = uuid4().hex
        with directory_descriptor(self.root) as parent:
            descriptor = os.open(
                transfer_id,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            os.close(descriptor)
        contract = FileTransfer(transfer_id, "upload", manifest)
        self.transfers[transfer_id] = _Transfer(contract)
        return contract

    async def create_download_async(self, snapshot: FileSnapshot) -> FileTransfer:
        self._admit(snapshot.manifest)
        transfer_id = uuid4().hex
        contract = FileTransfer(transfer_id, "download", snapshot.manifest)
        self.transfers[transfer_id] = _Transfer(contract)
        task = asyncio.create_task(
            asyncio.to_thread(
                copy_verified, snapshot.local_path, self.root / transfer_id, snapshot.manifest
            )
        )
        self.preparations.add(task)
        try:
            await asyncio.shield(task)
        except BaseException:
            await wait_for_settlement(task)
            self.release(transfer_id)
            raise
        finally:
            self.preparations.discard(task)
        if not self.valid():
            self.release(transfer_id)
            raise ValueError("generation revoked")
        return contract

    async def upload(self, transfer_id: str, chunks: AsyncIterator[bytes]) -> None:
        transfer = self._claim(transfer_id, "upload")
        task = asyncio.current_task()
        assert task is not None
        self.active.add(task)
        manifest = transfer.contract.manifest
        try:
            with _upload_descriptor(self.root / transfer_id) as descriptor:
                count, digest = 0, hashlib.sha256()
                async for chunk in chunks:
                    if not self.valid() or count + len(chunk) > manifest.byte_count:
                        raise ValueError("upload exceeds manifest or generation revoked")
                    for start in range(0, len(chunk), FILE_CHUNK_BYTES):
                        piece = chunk[start : start + FILE_CHUNK_BYTES]
                        await settled_io(_write_all, descriptor, piece)
                        digest.update(piece)
                        count += len(piece)
                if count != manifest.byte_count or digest.hexdigest() != manifest.sha256:
                    raise ValueError("upload is short or corrupt")
                await settled_io(os.fsync, descriptor)
            if not self.valid():
                raise ValueError("generation revoked")
            transfer.completed = True
        except BaseException:
            self.release(transfer_id)
            raise
        finally:
            self.active.discard(task)

    def claim_download(self, transfer_id: str) -> AsyncIterator[bytes]:
        transfer = self._claim(transfer_id, "download")

        async def chunks() -> AsyncIterator[bytes]:
            task = asyncio.current_task()
            assert task is not None
            self.active.add(task)
            transfer.readers += 1
            try:
                with regular_descriptor(self.root / transfer_id) as descriptor:
                    count = 0
                    while chunk := await settled_io(os.read, descriptor, TRANSFER_CHUNK_BYTES):
                        if (
                            not self.valid()
                            or count + len(chunk) > transfer.contract.manifest.byte_count
                        ):
                            raise ValueError("download revoked or exceeds manifest")
                        count += len(chunk)
                        yield chunk
                    if count != transfer.contract.manifest.byte_count:
                        raise ValueError("download is short")
            finally:
                self.active.discard(task)
                transfer.readers -= 1
                self.release(transfer_id)

        return chunks()

    def upload_path(self, transfer_id: str) -> Path:
        transfer = self.transfers.get(transfer_id)
        if (
            not self.valid()
            or transfer is None
            or not transfer.completed
            or transfer.consumed
            or transfer.contract.direction != "upload"
        ):
            raise ValueError("upload revoked or incomplete")
        transfer.consumed = True
        return self.root / transfer_id

    def release(self, transfer_id: str) -> None:
        transfer = self.transfers.get(transfer_id)
        if transfer is not None and transfer.readers:
            transfer.release_requested = True
            return
        if self.transfers.pop(transfer_id, None) is not None:
            with directory_descriptor(self.root) as parent:
                try:
                    os.unlink(transfer_id, dir_fd=parent)
                except FileNotFoundError:
                    pass

    async def close(self) -> None:
        self.closed = True
        tasks = tuple(self.active)
        for task in tasks:
            task.cancel()
        for task in tasks:
            await wait_for_settlement(task)
        for task in tuple(self.preparations):
            await wait_for_settlement(task)
        for transfer_id in tuple(self.transfers):
            self.release(transfer_id)


def _write_all(descriptor: int, content: bytes) -> None:
    while content:
        count = os.write(descriptor, content)
        if count <= 0:
            raise OSError("short file write")
        content = content[count:]


@contextmanager
def _upload_descriptor(path: Path) -> Iterator[int]:
    descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("upload reservation is not regular")
        yield descriptor
    finally:
        os.close(descriptor)


if TYPE_CHECKING:
    from planner.executor.link import Link


async def put_file(link: Link, slot: FileTransfer, path: Path) -> None:
    client = link.file_client()
    if slot.direction != "upload" or not re.fullmatch(r"[a-f0-9]{32}", slot.transfer_id):
        raise ValueError("invalid upload slot")

    async def chunks() -> AsyncIterator[bytes]:
        with regular_descriptor(path) as descriptor:
            remaining = slot.manifest.byte_count
            digest = hashlib.sha256()
            while remaining:
                content = await settled_io(
                    os.read, descriptor, min(TRANSFER_CHUNK_BYTES, remaining)
                )
                if not content:
                    raise ValueError("snapshot shorter than manifest")
                remaining -= len(content)
                digest.update(content)
                yield content
            if await settled_io(os.read, descriptor, 1):
                raise ValueError("snapshot exceeds manifest")
            if digest.hexdigest() != slot.manifest.sha256:
                raise ValueError("snapshot digest differs from manifest")

    headers = client.headers() | {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(slot.manifest.byte_count),
    }
    async with client.session.put(
        client.server_url + "/api/executor/files/" + slot.transfer_id,
        data=chunks(),
        headers=headers,
        allow_redirects=False,
        compress=False,
    ) as response:
        link.file_client()
        if response.status != 204:
            if 400 <= response.status < 500:
                raise ValueError("executor upload refused")
            raise ConnectionError("executor upload refused")


async def get_file(
    link: Link,
    slot: FileTransfer,
    path: Path,
    progress: Callable[[str, int], Awaitable[None]] | None = None,
) -> None:
    client = link.file_client()
    if slot.direction != "download" or not re.fullmatch(r"[a-f0-9]{32}", slot.transfer_id):
        raise ValueError("invalid download slot")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        async with client.session.get(
            client.server_url + "/api/executor/files/" + slot.transfer_id,
            headers=client.headers() | {"Accept-Encoding": "identity"},
            allow_redirects=False,
        ) as response:
            if response.status != 200 or response.headers.get("Content-Encoding"):
                raise ConnectionError("executor download refused")
            count, digest = 0, hashlib.sha256()
            async for chunk in response.content.iter_chunked(TRANSFER_CHUNK_BYTES):
                count += len(chunk)
                if count > slot.manifest.byte_count:
                    raise ValueError("download exceeds manifest")
                digest.update(chunk)
                await settled_io(_write_all, descriptor, chunk)
                if progress is not None and chunk:
                    await progress("download", count)
            if count != slot.manifest.byte_count:
                raise ValueError("download shorter than manifest")
            if digest.hexdigest() != slot.manifest.sha256:
                raise ValueError("download digest differs from manifest")
            link.file_client()
            if progress is not None:
                await progress("finalization", count)
            await settled_io(os.fsync, descriptor)
    finally:
        os.close(descriptor)
