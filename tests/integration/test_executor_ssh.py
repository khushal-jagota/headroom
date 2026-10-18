"""Real OpenSSH closes the stdio harness gap for both Unix forwarding directions."""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
from dataclasses import replace
from pathlib import Path

import asyncssh
import pytest
from tests.support.executor_sshd import isolated_sshd

from planner.executor.contracts import SftpEndpoint
from planner.executor.ssh import (
    close_connection,
    connect_outer,
    connect_sftp,
    download_slot,
    generate_ephemeral_key,
    upload_snapshot,
)


class KeyOnlyServer(asyncssh.SSHServer):
    def __init__(self, key: asyncssh.SSHKey) -> None:
        self.key = key

    def begin_auth(self, username: str) -> bool:
        return True

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        return username == "generation" and key.public_data == self.key.public_data


def test_openssh_public_bridge_pinned_sftp_and_reverse_streamlocal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write = asyncssh.SFTPClientFile.write
    outstanding = 0
    peak = 0

    async def observed_write(
        remote: asyncssh.SFTPClientFile,
        data: bytes | str,
        offset: int | None = None,
    ) -> int:
        nonlocal outstanding, peak
        outstanding += 1
        peak = max(peak, outstanding)
        try:
            if isinstance(data, bytes):
                return await original_write(remote, data, offset)
            return await original_write(remote, data, offset)
        finally:
            outstanding -= 1

    monkeypatch.setattr(asyncssh.SFTPClientFile, "write", observed_write)

    async def exercise(root: Path) -> None:
        with isolated_sshd(root / "daemon") as config:
            outer = await connect_outer(config)
            server_key, client_key = generate_ephemeral_key(), generate_ephemeral_key()
            listener_socket = socket.socket(socket.AF_UNIX)
            remote_path = root / "restricted"
            listener_socket.bind(str(remote_path))
            listener_socket.listen()
            listener_socket.setblocking(False)
            export = root / "export"
            export.mkdir()
            (export / "slot").write_bytes(b"slot bytes")
            server = await asyncssh.listen(
                sock=listener_socket,
                server_factory=lambda: KeyOnlyServer(client_key),
                server_host_keys=[server_key],
                sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=os.fsencode(export)),
                sftp_version=3,
            )
            endpoint = SftpEndpoint(
                str(remote_path), server_key.export_public_key().decode(), "generation"
            )
            run = root / ("long-persistent-root-" * 6) / "run"
            run.mkdir(mode=0o700, parents=True)
            try:
                async with connect_sftp(outer, endpoint, client_key, run) as sftp:
                    assert sftp.version == 3
                    (export / "upload").touch()
                    source = root / "snapshot"
                    content = b"bytes" * 300000
                    source.write_bytes(content)
                    await upload_snapshot(sftp, source, "/upload")
                    assert (export / "upload").read_bytes() == content
                    assert 1 < peak <= 16
                    assert outstanding == 0
                    downloaded = root / "download"
                    await download_slot(sftp, "/upload", downloaded)
                    assert downloaded.read_bytes() == content
                    with pytest.raises(FileExistsError):
                        await download_slot(sftp, "/upload", downloaded)
                    async with sftp.open("/slot", "r") as handle:
                        assert bytes(await handle.read(), "utf-8") == b"slot bytes"
                    with pytest.raises(asyncssh.SFTPNoSuchFile):
                        await sftp.stat("/../daemon/identity")
                assert not list(run.iterdir())
                wrong = replace(
                    endpoint,
                    server_public_key=generate_ephemeral_key().export_public_key().decode(),
                )
                with pytest.raises(asyncssh.HostKeyNotVerifiable):
                    async with connect_sftp(outer, wrong, client_key, run):
                        pytest.fail("wrong generation host key was accepted")
                with pytest.raises(asyncssh.PermissionDenied):
                    async with connect_sftp(outer, endpoint, generate_ephemeral_key(), run):
                        pytest.fail("wrong generation credential was accepted")
                assert not list(run.iterdir())

                async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                    writer.write(await reader.read(64))
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()

                target = await asyncio.start_server(echo, "127.0.0.1", 0)
                preview_path = root / "preview"
                forward = await outer.forward_remote_path_to_port(
                    str(preview_path),
                    "127.0.0.1",
                    target.sockets[0].getsockname()[1],
                )
                try:
                    reader, writer = await asyncio.open_unix_connection(str(preview_path))
                    writer.write(b"preview bytes")
                    await writer.drain()
                    assert await reader.read() == b"preview bytes"
                    writer.close()
                    await writer.wait_closed()
                finally:
                    forward.close()
                    await forward.wait_closed()
                    target.close()
                    await target.wait_closed()
                with pytest.raises((FileNotFoundError, ConnectionRefusedError)):
                    await asyncio.open_unix_connection(str(preview_path))
            finally:
                server.close()
                await server.wait_closed()
                await close_connection(outer)

    with tempfile.TemporaryDirectory(prefix="panels-ssh-") as tmp:
        asyncio.run(exercise(Path(tmp)))


def test_openssh_strict_host_key_and_no_ambient_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        with isolated_sshd(tmp_path / "daemon") as config:
            # A configured malicious alias/proxy must not execute. An ambient agent
            # must not be consulted when the explicit key cannot authenticate.
            home = tmp_path / "home"
            (home / ".ssh").mkdir(parents=True)
            # A usable default identity must never rescue a wrong explicit key.
            (home / ".ssh" / "id_ed25519").write_bytes(config.identity_file.read_bytes())
            marker = tmp_path / "proxy-executed"
            (home / ".ssh" / "config").write_text(f"Host *\n ProxyCommand touch {marker}\n")
            monkeypatch.setenv("HOME", str(home))
            attempts = []

            async def agent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                attempts.append(True)
                writer.close()

            ambient_socket = tmp_path / "agent"
            agent_server = await asyncio.start_unix_server(agent, str(ambient_socket))
            monkeypatch.setenv("SSH_AUTH_SOCK", str(ambient_socket))
            try:
                connection = await connect_outer(config)
                await close_connection(connection)
                wrong_identity = tmp_path / "wrong"
                generate_ephemeral_key().write_private_key(wrong_identity)
                with pytest.raises(asyncssh.PermissionDenied):
                    await connect_outer(replace(config, identity_file=wrong_identity))
                assert not attempts
                assert not marker.exists()
                config.known_hosts_file.write_text("")
                with pytest.raises(asyncssh.HostKeyNotVerifiable):
                    await connect_outer(config)
                replacement = generate_ephemeral_key().export_public_key()
                config.known_hosts_file.write_bytes(
                    f"[127.0.0.1]:{config.port} ".encode() + replacement
                )
                with pytest.raises(asyncssh.HostKeyNotVerifiable):
                    await connect_outer(config)
            finally:
                agent_server.close()
                await agent_server.wait_closed()

    asyncio.run(exercise())
