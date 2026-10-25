"""Real inner SSH proves capabilities are enforced below the transport caller."""

from __future__ import annotations

import asyncio
import hashlib
import socket
import struct
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

import asyncssh
import pytest

from planner.executor.contracts import ExecutorIdentity, FileManifest, FileScope, FileSnapshot
from planner.executor.files import copy_verified
from planner.executor.sftp import RestrictedSftpService
from planner.executor.vps import _generation_root


def _string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


class _WireSftp:
    """Send real extension packets without AsyncSSH's private client API."""

    def __init__(self, process: asyncssh.SSHClientProcess[bytes]) -> None:
        self.process = process
        self.request_id = 0

    async def packet(self, payload: bytes) -> bytes:
        self.process.stdin.write(struct.pack(">I", len(payload)) + payload)
        await self.process.stdin.drain()
        length = struct.unpack(">I", await self.process.stdout.readexactly(4))[0]
        return await self.process.stdout.readexactly(length)

    async def request(self, operation: int, payload: bytes) -> bytes:
        self.request_id += 1
        response = await self.packet(
            bytes([operation]) + struct.pack(">I", self.request_id) + payload
        )
        assert struct.unpack(">I", response[1:5])[0] == self.request_id
        return response

    async def open(self, token: str, flags: int) -> bytes:
        response = await self.request(
            asyncssh.FXP_OPEN, _string(token.encode()) + struct.pack(">II", flags, 0)
        )
        assert response[0] == asyncssh.FXP_HANDLE
        length = struct.unpack(">I", response[5:9])[0]
        return response[9 : 9 + length]

    async def copy(
        self,
        source: bytes,
        destination: bytes,
        length: int,
        offset: int = 0,
        destination_offset: int = 0,
    ) -> int:
        response = await self.request(
            asyncssh.FXP_EXTENDED,
            _string(b"copy-data")
            + _string(source)
            + struct.pack(">QQ", offset, length)
            + _string(destination)
            + struct.pack(">Q", destination_offset),
        )
        assert response[0] == asyncssh.FXP_STATUS
        return int(struct.unpack(">I", response[5:9])[0])


def _manifest(data: bytes) -> FileManifest:
    return FileManifest(
        "publication",
        FileScope.ticket,
        "ticket",
        "nested/file.txt",
        hashlib.sha256(data).hexdigest(),
        len(data),
    )


async def _connect(
    service: RestrictedSftpService, key: asyncssh.SSHKey
) -> asyncssh.SSHClientConnection:
    endpoint = await service.start()
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(endpoint.socket_path)
    return await asyncssh.connect(
        sock=sock,
        username=endpoint.username,
        config=None,
        client_keys=[key],
        known_hosts=([asyncssh.import_public_key(endpoint.server_public_key)], [], []),
        agent_path=None,
        password_auth=False,
        kbdint_auth=False,
    )


def _service(
    tmp_path: Path, key: asyncssh.SSHKey, valid: Callable[[], bool] = lambda: True
) -> RestrictedSftpService:
    identity = ExecutorIdentity(5, "a" * 40, 2, "executor", 1)
    return RestrictedSftpService(
        identity, tmp_path / "slots", key.export_public_key().decode(), valid
    )


def test_real_sftp_slot_boundary_and_sealing(tmp_path: Path, executor_transport_home: Path) -> None:
    async def exercise() -> None:
        key = asyncssh.generate_private_key("ssh-ed25519")
        root = _generation_root()
        generation = Path(root.name)
        assert generation.parent == executor_transport_home / ".cache/panels/ssh"
        service = _service(generation, key)
        connection = await _connect(service, key)
        try:
            async with connection.start_sftp_client(sftp_version=3) as client:
                data = b"contents" * 131072
                manifest = _manifest(data)
                upload = service.create_upload(manifest)
                for path in ("/etc/passwd", "../" + upload.token, "/" + upload.token):
                    with pytest.raises(asyncssh.SFTPPermissionDenied):
                        await client.stat(path)
                for operation in (
                    lambda: client.open(upload.token, "wb"),
                    lambda: client.remove(upload.token),
                    lambda: client.setstat(upload.token, asyncssh.SFTPAttrs(size=0)),
                    lambda: client.realpath(upload.token),
                ):
                    with pytest.raises(asyncssh.SFTPPermissionDenied):
                        await operation()
                handle = await client.open(upload.token, asyncssh.FXF_WRITE, encoding=None)
                offsets = range(0, len(data), 65536)
                await asyncio.gather(
                    *(handle.write(data[offset : offset + 65536], offset) for offset in offsets)
                )
                sealed = await service.seal_upload(upload.token)
                assert sealed.read_bytes() == data
                with pytest.raises(asyncssh.SFTPPermissionDenied):
                    await handle.write(b"x", 0)
                await handle.close()
                service.release(upload.token)
                download = service.create_download(FileSnapshot(manifest, sealed))
                async with client.open(download.token, asyncssh.FXF_READ, encoding=None) as reader:
                    blocks = cast(
                        list[bytes],
                        await asyncio.gather(*(reader.read(65536, offset) for offset in offsets)),
                    )
                    assert blocks == [data[offset : offset + 65536] for offset in offsets]
                with pytest.raises(asyncssh.ChannelOpenError):
                    await connection.open_connection("127.0.0.1", 22)
                with pytest.raises(asyncssh.ChannelOpenError):
                    await connection.open_unix_connection("/tmp/arbitrary")
                with pytest.raises(asyncssh.ChannelListenError):
                    await connection.forward_remote_port("", 0, "127.0.0.1", 22)
                with pytest.raises(asyncssh.ChannelOpenError):
                    await connection.create_process("id")
                with pytest.raises(asyncssh.ChannelOpenError):
                    await connection.create_process()
        finally:
            connection.close()
            await connection.wait_closed()
            await service.close()
            root.cleanup()
        assert not service.socket_path.exists()
        assert not generation.exists()

    asyncio.run(exercise())


def test_revocation_handle_limits_and_symlink_refusal(tmp_path: Path) -> None:
    async def exercise() -> None:
        key = asyncssh.generate_private_key("ssh-ed25519")
        current = True
        service = _service(tmp_path, key, lambda: current)
        connection = await _connect(service, key)
        try:
            async with connection.start_sftp_client(sftp_version=3) as client:
                slot = service.create_upload(_manifest(b"test"))
                handles = [await client.open(slot.token, asyncssh.FXF_WRITE) for _ in range(4)]
                with pytest.raises(asyncssh.SFTPFailure):
                    await client.open(slot.token, asyncssh.FXF_WRITE)
                current = False
                with pytest.raises(asyncssh.SFTPPermissionDenied):
                    await handles[0].write("test")
                with pytest.raises(ValueError, match="revoked"):
                    await service.seal_upload(slot.token)
                for handle in handles:
                    await handle.close()
        finally:
            await service.close()
            connection.close()
            await connection.wait_closed()
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
        unsafe = RestrictedSftpService(
            service.identity,
            tmp_path / "link" / "slots",
            key.export_public_key().decode(),
            lambda: True,
        )
        with pytest.raises(OSError):
            await unsafe.start()

    asyncio.run(exercise())


def test_real_copy_extension_uses_only_bounded_authorized_handles(tmp_path: Path) -> None:
    async def exercise() -> None:
        key = asyncssh.generate_private_key("ssh-ed25519")
        current = True
        service = _service(tmp_path, key, lambda: current)
        connection = await _connect(service, key)
        try:
            process = await connection.create_process(subsystem="sftp", encoding=None)
            wire = _WireSftp(process)
            version = await wire.packet(bytes([asyncssh.FXP_INIT]) + struct.pack(">I", 3))
            assert version[0] == asyncssh.FXP_VERSION
            data = b"x" * (2 * 65536)
            source_path = tmp_path / "source"
            source_path.write_bytes(data)
            manifest = _manifest(data)
            download = service.create_download(FileSnapshot(manifest, source_path))
            upload = service.create_upload(manifest)
            reader = await wire.open(download.token, asyncssh.FXF_READ)
            writer = await wire.open(upload.token, asyncssh.FXF_WRITE)

            # limits@openssh.com is library-owned advisory negotiation. The
            # normal hooks enforce our smaller bounds regardless of this reply.
            limits = await wire.request(asyncssh.FXP_EXTENDED, _string(b"limits@openssh.com"))
            assert limits[0] == asyncssh.FXP_EXTENDED_REPLY
            assert len(limits[5:]) == 32

            path = _string(download.token.encode())
            for extension, payload in (
                (b"posix-rename@openssh.com", path + path),
                (b"statvfs@openssh.com", path),
                (b"fstatvfs@openssh.com", _string(reader)),
                (b"hardlink@openssh.com", path + path),
                (b"fsync@openssh.com", _string(writer)),
                (b"lsetstat@openssh.com", path + struct.pack(">I", 0)),
                (b"ranges@asyncssh.com", _string(reader) + struct.pack(">QQ", 0, 1)),
            ):
                response = await wire.request(asyncssh.FXP_EXTENDED, _string(extension) + payload)
                assert response[0] == asyncssh.FXP_STATUS
                assert struct.unpack(">I", response[5:9])[0] == asyncssh.FX_PERMISSION_DENIED
            for extension in (
                b"filename-translation-control",
                b"vendor-id",
                b"expand-path@openssh.com",
            ):
                response = await wire.request(asyncssh.FXP_EXTENDED, _string(extension))
                assert response[0] == asyncssh.FXP_STATUS
                assert struct.unpack(">I", response[5:9])[0] == asyncssh.FX_OP_UNSUPPORTED

            assert await wire.copy(reader, writer, 65536) == asyncssh.FX_OK
            assert await wire.copy(reader, writer, 65536, 65536, 65536) == asyncssh.FX_OK
            assert service.slots[upload.token].path.read_bytes() == data
            denied = asyncssh.FX_PERMISSION_DENIED
            # The library asks read() for 256 KiB for zero-length-to-EOF and
            # large copies. Both refuse, even for a file shorter than that.
            assert await wire.copy(reader, writer, 0) == denied
            assert await wire.copy(reader, writer, 65537) == denied
            assert await wire.copy(reader, writer, 2**64 - 1) == denied
            assert await wire.copy(reader, writer, 1, len(data) + 1) == denied
            assert await wire.copy(reader, writer, 1, destination_offset=len(data)) == denied
            assert await wire.copy(writer, reader, 1) == denied
            assert await wire.copy(reader, reader, 1) == denied

            sealed = await service.seal_upload(upload.token)
            assert sealed.read_bytes() == data
            assert await wire.copy(reader, writer, 1) == denied
            other = service.create_upload(manifest)
            other_writer = await wire.open(other.token, asyncssh.FXF_WRITE)
            current = False
            assert await wire.copy(reader, other_writer, 1) == denied
            service.release(download.token)
            assert await wire.copy(reader, other_writer, 1) == denied
        finally:
            await service.close()
            connection.close()
            await connection.wait_closed()

    asyncio.run(exercise())


def test_snapshot_copy_cancellation_drains_thread_before_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    finish = threading.Event()

    def paused_copy(source: Path, destination: Path, manifest: FileManifest) -> None:
        started.set()
        assert finish.wait(5)
        copy_verified(source, destination, manifest)

    monkeypatch.setattr("planner.executor.sftp.copy_verified", paused_copy)

    async def exercise() -> None:
        key = asyncssh.generate_private_key("ssh-ed25519")
        service = _service(tmp_path, key)
        await service.start()
        source = tmp_path / "source"
        source.write_bytes(b"test")
        transfer = asyncio.create_task(
            service.create_download_async(FileSnapshot(_manifest(b"test"), source))
        )
        await asyncio.wait_for(asyncio.to_thread(started.wait), 5)
        transfer.cancel()
        shutdown = asyncio.create_task(service.close())
        try:
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert not transfer.done()
        finally:
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await transfer
            await shutdown
        assert not service.slots
        assert not list(service.root.iterdir())

    asyncio.run(exercise())
