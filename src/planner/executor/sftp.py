"""Generation-bound SFTP capability service. No client path maps to a host path."""

from __future__ import annotations

import asyncio
import os
import re
import socket
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import asyncssh

from planner.executor.configuration import (
    SFTP_BLOCK_BYTES,
    SFTP_MAX_HANDLES,
    SFTP_MAX_TRANSFERS,
    SSH_CLOSE_TIMEOUT_SECONDS,
    SSH_LOGIN_TIMEOUT_SECONDS,
)
from planner.executor.contracts import (
    ExecutorIdentity,
    FileManifest,
    FileSnapshot,
    FileTransferSlot,
    SftpEndpoint,
)
from planner.executor.files import copy_verified, directory_descriptor, regular_descriptor


@dataclass(eq=False)
class _Handle:
    descriptor: int
    slot: _Slot
    closed: bool = False

    def seek(self, *args: Any) -> int:
        raise asyncssh.SFTPPermissionDenied("sparse ranges refused")


@dataclass
class _Slot:
    contract: FileTransferSlot
    path: Path
    writable: bool
    handles: set[_Handle] = field(default_factory=set)


class _Authentication(asyncssh.SSHServer):
    def __init__(self, service: RestrictedSftpService) -> None:
        self.service = service
        self.connection: asyncssh.SSHServerConnection | None = None

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self.connection = conn
        if self.service.connection is not None or not self.service.valid():
            conn.abort()
        else:
            self.service.connection = conn

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        return (
            self.service.valid()
            and username == self.service.username
            and key == self.service.client_key
        )


class _RestrictedServer(asyncssh.SFTPServer):
    def __init__(
        self, channel: asyncssh.SSHServerChannel[bytes], service: RestrictedSftpService
    ) -> None:
        super().__init__(channel)
        self.service = service

    def _slot(self, path: bytes) -> _Slot:
        if not self.service.valid():
            raise asyncssh.SFTPPermissionDenied("generation revoked")
        try:
            return self.service.slots[path.decode("ascii")]
        except (KeyError, UnicodeError):
            raise asyncssh.SFTPPermissionDenied("unknown slot") from None

    def _handle(self, obj: object) -> _Handle:
        handle = cast(_Handle, obj)
        if (
            handle.closed
            or not self.service.valid()
            or self.service.slots.get(handle.slot.contract.token) is not handle.slot
            or handle not in handle.slot.handles
        ):
            raise asyncssh.SFTPPermissionDenied("handle revoked")
        return handle

    def open(self, path: bytes, pflags: int, attrs: asyncssh.SFTPAttrs) -> object:
        slot = self._slot(path)
        allowed = asyncssh.FXF_WRITE if slot.contract.direction == "upload" else asyncssh.FXF_READ
        if pflags != allowed or (allowed == asyncssh.FXF_WRITE and not slot.writable):
            raise asyncssh.SFTPPermissionDenied("slot direction or flags refused")
        if sum(len(entry.handles) for entry in self.service.slots.values()) >= SFTP_MAX_HANDLES:
            raise asyncssh.SFTPFailure("slot handle limit")
        with directory_descriptor(slot.path.parent) as parent:
            descriptor = os.open(
                slot.path.name,
                (os.O_WRONLY if slot.writable else os.O_RDONLY) | os.O_NOFOLLOW,
                dir_fd=parent,
            )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise asyncssh.SFTPPermissionDenied("slot is not regular")
        handle = _Handle(descriptor, slot)
        slot.handles.add(handle)
        return handle

    def close(self, file_obj: object) -> None:
        handle = cast(_Handle, file_obj)
        if not handle.closed:
            handle.closed = True
            os.close(handle.descriptor)
            handle.slot.handles.discard(handle)

    async def read(self, file_obj: object, offset: int, size: int) -> bytes:
        # AsyncSSH also routes copy-data through these public hooks. Yield before
        # every operation so repeated internal calls cannot starve revocation.
        # Its 256 KiB copy blocks (including length=0) retain the 64 KiB refusal.
        await asyncio.sleep(0)
        handle = self._handle(file_obj)
        if (
            handle.slot.contract.direction != "download"
            or offset < 0
            or not 0 <= size <= SFTP_BLOCK_BYTES
        ):
            raise asyncssh.SFTPPermissionDenied("read refused")
        remaining = handle.slot.contract.manifest.byte_count - offset
        if remaining < 0:
            raise asyncssh.SFTPPermissionDenied("read exceeds manifest range")
        return os.pread(handle.descriptor, min(size, remaining), offset)

    async def write(self, file_obj: object, offset: int, data: bytes) -> int:
        await asyncio.sleep(0)
        handle = self._handle(file_obj)
        if (
            handle.slot.contract.direction != "upload"
            or not handle.slot.writable
            or offset < 0
            or len(data) > SFTP_BLOCK_BYTES
            or offset + len(data) > handle.slot.contract.manifest.byte_count
        ):
            raise asyncssh.SFTPPermissionDenied("write refused")
        view = memoryview(data)
        while view:
            written = os.pwrite(handle.descriptor, view, offset)
            if not written:
                raise asyncssh.SFTPFailure("write made no progress")
            offset += written
            view = view[written:]
        return len(data)

    def stat(self, path: bytes) -> asyncssh.SFTPAttrs:
        slot = self._slot(path)
        with regular_descriptor(slot.path) as descriptor:
            return asyncssh.SFTPAttrs.from_local(os.fstat(descriptor))

    lstat = stat

    def fstat(self, file_obj: object) -> asyncssh.SFTPAttrs:
        return asyncssh.SFTPAttrs.from_local(os.fstat(self._handle(file_obj).descriptor))

    def _deny(self, *args: Any, **kwargs: Any) -> Any:
        raise asyncssh.SFTPPermissionDenied("operation refused")

    # Never inherit filesystem behavior, including extension implementations.
    map_path = _deny
    reverse_map_path = _deny
    open56 = _deny
    setstat = _deny
    lsetstat = _deny
    fsetstat = _deny
    scandir = _deny
    remove = _deny
    mkdir = _deny
    rmdir = _deny
    realpath = _deny
    rename = _deny
    readlink = _deny
    symlink = _deny
    link = _deny
    lock = _deny
    unlock = _deny
    posix_rename = _deny
    statvfs = _deny
    fstatvfs = _deny
    fsync = _deny


class RestrictedSftpService:
    def __init__(
        self,
        identity: ExecutorIdentity,
        root: Path,
        client_public_key: str,
        valid: Callable[[], bool],
    ) -> None:
        self.identity = identity
        self.root = root
        self.client_key = asyncssh.import_public_key(client_public_key)
        self._valid = valid
        self.closed = False
        self.username = "slots-" + uuid4().hex
        self.server_key = asyncssh.generate_private_key("ssh-ed25519")
        self.connection: asyncssh.SSHServerConnection | None = None
        self.listener: asyncssh.SSHAcceptor | None = None
        self.slots: dict[str, _Slot] = {}
        self.channel_started = False
        self.sealed_paths: set[Path] = set()
        self.seals: set[asyncio.Task[None]] = set()
        self.socket_identity: tuple[int, int] | None = None
        self.socket_path = root / "sftp.sock"

    def valid(self) -> bool:
        return not self.closed and self._valid()

    async def start(self) -> SftpEndpoint:
        with directory_descriptor(self.root, create=True) as descriptor:
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("SFTP directory must be private and owned")
        if len(os.fsencode(self.socket_path)) >= 104:
            raise ValueError("SFTP socket path too long")
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(self.socket_path))
            info = self.socket_path.lstat()
            self.socket_identity = (info.st_dev, info.st_ino)
            sock.listen(1)
            self.listener = await asyncssh.listen(
                sock=sock,
                config=None,
                server_factory=lambda: _Authentication(self),
                server_host_keys=[self.server_key],
                sftp_factory=self._server,
                sftp_version=3,
                allow_scp=False,
                login_timeout=SSH_LOGIN_TIMEOUT_SECONDS,
                public_key_auth=True,
                password_auth=False,
                kbdint_auth=False,
                host_based_auth=False,
                gss_auth=False,
                gss_kex=False,
                agent_forwarding=False,
                x11_forwarding=False,
            )
        except BaseException:
            sock.close()
            raise
        return SftpEndpoint(
            str(self.socket_path), self.server_key.export_public_key().decode(), self.username
        )

    def _server(self, channel: asyncssh.SSHServerChannel[bytes]) -> _RestrictedServer:
        if self.channel_started or not self.valid():
            channel.get_connection().abort()
            raise asyncssh.SFTPPermissionDenied("one SFTP channel per generation")
        self.channel_started = True
        return _RestrictedServer(channel, self)

    def _admit(self, manifest: FileManifest) -> None:
        if not self.valid():
            raise ValueError("generation revoked")
        if len(self.slots) >= SFTP_MAX_TRANSFERS:
            raise ValueError("transfer limit")
        if manifest.byte_count < 0 or not re.fullmatch("[a-f0-9]{64}", manifest.sha256):
            raise ValueError("invalid manifest length or digest")

    def create_upload(self, manifest: FileManifest) -> FileTransferSlot:
        self._admit(manifest)
        token = uuid4().hex
        path = self.root / token
        with directory_descriptor(self.root) as parent:
            descriptor = os.open(
                token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
            )
            os.close(descriptor)
        contract = FileTransferSlot(token, "upload", manifest)
        self.slots[token] = _Slot(contract, path, True)
        return contract

    def create_download(self, snapshot: FileSnapshot) -> FileTransferSlot:
        self._admit(snapshot.manifest)
        token = uuid4().hex
        path = self.root / token
        copy_verified(snapshot.local_path, path, snapshot.manifest)
        if not self.valid():
            path.unlink(missing_ok=True)
            raise ValueError("generation revoked")
        contract = FileTransferSlot(token, "download", snapshot.manifest)
        self.slots[token] = _Slot(contract, path, False)
        return contract

    async def create_download_async(self, snapshot: FileSnapshot) -> FileTransferSlot:
        """Reserve admission before filesystem work and drain it before cleanup."""
        self._admit(snapshot.manifest)
        token = uuid4().hex
        path = self.root / token
        contract = FileTransferSlot(token, "download", snapshot.manifest)
        self.slots[token] = _Slot(contract, path, False)
        task = asyncio.create_task(
            asyncio.to_thread(copy_verified, snapshot.local_path, path, snapshot.manifest)
        )
        self.seals.add(task)
        try:
            await asyncio.shield(task)
        except BaseException:
            await asyncio.gather(task, return_exceptions=True)
            self.release(token)
            raise
        finally:
            self.seals.discard(task)
        if not self.valid():
            self.release(token)
            raise ValueError("generation revoked")
        return contract

    async def seal_upload(self, token: str) -> Path:
        slot = self.slots[token]
        if not self.valid() or slot.contract.direction != "upload" or not slot.writable:
            raise ValueError("upload revoked")
        slot.writable = False
        self._close_handles(slot)
        destination = self.root / (uuid4().hex + ".sealed")
        self.sealed_paths.add(destination)
        task = asyncio.create_task(
            asyncio.to_thread(copy_verified, slot.path, destination, slot.contract.manifest)
        )
        self.seals.add(task)
        try:
            await asyncio.shield(task)
        except BaseException:
            # A canceled coroutine cannot cancel filesystem work in its thread.
            # Wait before removal so no orphan can appear after closure.
            await task
            destination.unlink(missing_ok=True)
            self.sealed_paths.discard(destination)
            raise
        finally:
            self.seals.discard(task)
        if not self.valid():
            destination.unlink(missing_ok=True)
            self.sealed_paths.discard(destination)
            raise ValueError("generation revoked")
        return destination

    @staticmethod
    def _close_handles(slot: _Slot) -> None:
        for handle in tuple(slot.handles):
            handle.closed = True
            os.close(handle.descriptor)
        slot.handles.clear()

    def release(self, token: str) -> None:
        slot = self.slots.pop(token, None)
        if slot is not None:
            slot.writable = False
            self._close_handles(slot)
            with directory_descriptor(self.root) as parent:
                try:
                    os.unlink(slot.path.name, dir_fd=parent)
                except FileNotFoundError:
                    pass

    async def close(self) -> None:
        self.closed = True
        if self.seals:
            await asyncio.gather(*self.seals, return_exceptions=True)
        for token in tuple(self.slots):
            self.release(token)
        if self.connection is not None:
            self.connection.close()
            try:
                await asyncio.wait_for(self.connection.wait_closed(), SSH_CLOSE_TIMEOUT_SECONDS)
            except TimeoutError:
                self.connection.abort()
        if self.listener is not None:
            self.listener.close()
            await self.listener.wait_closed()
        with directory_descriptor(self.root) as parent:
            for path in self.sealed_paths:
                try:
                    os.unlink(path.name, dir_fd=parent)
                except FileNotFoundError:
                    pass
            self.sealed_paths.clear()
            try:
                info = os.stat(self.socket_path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if (
                    stat.S_ISSOCK(info.st_mode)
                    and info.st_uid == os.getuid()
                    and (info.st_dev, info.st_ino) == self.socket_identity
                ):
                    os.unlink(self.socket_path.name, dir_fd=parent)
