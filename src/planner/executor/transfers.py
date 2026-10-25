"""Private generation transfer allocation, independent of the account SFTP service."""

from __future__ import annotations

import asyncio
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from planner.executor.configuration import SFTP_MAX_TRANSFERS
from planner.executor.contracts import ExecutorIdentity, FileManifest, FileSnapshot, FileTransfer
from planner.executor.files import copy_verified, directory_descriptor


@dataclass
class _Transfer:
    contract: FileTransfer
    completed: bool = False


class GenerationTransfers:
    def __init__(self, identity: ExecutorIdentity, root: Path, valid: Callable[[], bool]) -> None:
        self.identity = identity
        self.root = root
        self._valid = valid
        self.closed = False
        self.transfers: dict[str, _Transfer] = {}
        self.preparations: set[asyncio.Task[None]] = set()
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
        if len(self.transfers) >= SFTP_MAX_TRANSFERS:
            raise ValueError("transfer limit")
        if manifest.byte_count < 0 or not re.fullmatch("[a-f0-9]{64}", manifest.sha256):
            raise ValueError("invalid manifest length or digest")

    def create_upload(self, manifest: FileManifest) -> FileTransfer:
        self._admit(manifest)
        identity = uuid4().hex
        with directory_descriptor(self.root) as parent:
            descriptor = os.open(
                identity,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            os.close(descriptor)
        contract = FileTransfer(identity, str(self.root / identity), "upload", manifest)
        self.transfers[identity] = _Transfer(contract)
        return contract

    async def create_download_async(self, snapshot: FileSnapshot) -> FileTransfer:
        self._admit(snapshot.manifest)
        identity = uuid4().hex
        path = self.root / identity
        contract = FileTransfer(identity, str(path), "download", snapshot.manifest)
        self.transfers[identity] = _Transfer(contract)
        task = asyncio.create_task(
            asyncio.to_thread(
                copy_verified,
                snapshot.local_path,
                path,
                snapshot.manifest,
            )
        )
        self.preparations.add(task)
        try:
            await asyncio.shield(task)
        except BaseException:
            await asyncio.gather(task, return_exceptions=True)
            self.release(identity)
            raise
        finally:
            self.preparations.discard(task)
        if not self.valid():
            self.release(identity)
            raise ValueError("generation revoked")
        return contract

    def upload_path(self, transfer_id: str) -> Path:
        transfer = self.transfers.get(transfer_id)
        if (
            not self.valid()
            or transfer is None
            or transfer.completed
            or transfer.contract.direction != "upload"
        ):
            raise ValueError("upload revoked")
        transfer.completed = True
        return Path(transfer.contract.path)

    def release(self, transfer_id: str) -> None:
        transfer = self.transfers.pop(transfer_id, None)
        if transfer is not None:
            with directory_descriptor(self.root) as parent:
                try:
                    os.unlink(Path(transfer.contract.path).name, dir_fd=parent)
                except FileNotFoundError:
                    pass

    async def close(self) -> None:
        self.closed = True
        if self.preparations:
            await asyncio.gather(*self.preparations, return_exceptions=True)
        for transfer_id in tuple(self.transfers):
            self.release(transfer_id)
