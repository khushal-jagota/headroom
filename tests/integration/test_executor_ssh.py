"""Real OpenSSH closes the stdio harness gap for both Unix forwarding directions."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import asyncssh
import pytest
from tests.support.executor_sshd import isolated_sshd

from planner.executor.contracts import FileManifest, FileScope, FileTransfer
from planner.executor.ssh import (
    close_connection,
    connect_outer,
    connect_sftp,
    download_transfer,
    upload_snapshot,
)


def test_openssh_direct_sftp_with_concurrent_relay_and_reverse_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    import time

    original_write = asyncssh.SFTPClientFile.write
    original_read = asyncssh.SFTPClientFile.read
    read_outstanding = 0
    read_peak = 0
    outstanding = 0
    peak = 0
    upload_active, upload_echo = asyncio.Event(), asyncio.Event()
    download_active, download_echo = asyncio.Event(), asyncio.Event()
    transfers_done = asyncio.Event()

    async def observed_write(
        remote: asyncssh.SFTPClientFile,
        data: bytes | str,
        offset: int | None = None,
    ) -> int:
        nonlocal outstanding, peak
        outstanding += 1
        peak = max(peak, outstanding)
        assert len(data) <= 65536
        try:
            if isinstance(data, bytes):
                result = await original_write(remote, data, offset)
            else:
                result = await original_write(remote, data, offset)
            upload_active.set()
            await upload_echo.wait()
            return result
        finally:
            outstanding -= 1

    async def observed_read(
        remote: asyncssh.SFTPClientFile,
        size: int = -1,
        offset: int | None = None,
    ) -> bytes | str:
        nonlocal read_outstanding, read_peak
        assert 0 < size <= 65536
        read_outstanding += 1
        read_peak = max(read_peak, read_outstanding)
        try:
            result = await original_read(remote, size, offset)
            download_active.set()
            await download_echo.wait()
            return result
        finally:
            read_outstanding -= 1

    monkeypatch.setattr(asyncssh.SFTPClientFile, "read", observed_read)
    monkeypatch.setattr(asyncssh.SFTPClientFile, "write", observed_write)

    async def exercise(root: Path) -> None:
        started = time.monotonic()
        with isolated_sshd(root / "daemon") as config:
            outer = await connect_outer(config)
            try:
                async with connect_sftp(outer) as sftp:
                    assert sftp.version == 3
                    content = b"bytes" * 300000
                    manifest = FileManifest(
                        "publication",
                        FileScope.ticket,
                        "ticket",
                        "file",
                        hashlib.sha256(content).hexdigest(),
                        len(content),
                    )
                    source = root / "snapshot"
                    source.write_bytes(content)
                    remote = root / "upload"
                    remote.touch(mode=0o600)
                    upload = FileTransfer("upload", str(remote), "upload", manifest)
                    download = replace(upload, direction="download")
                    relay = await outer.create_process("cat", encoding=None)
                    relayed: list[int] = []
                    relay_directions: set[str] = set()

                    async def exercise_relay() -> None:
                        sequence = 0
                        while not transfers_done.is_set():
                            message = f"relay-{sequence}\n".encode()
                            relay.stdin.write(message)
                            await relay.stdin.drain()
                            assert await relay.stdout.readexactly(len(message)) == message
                            relayed.append(sequence)
                            sequence += 1
                            if upload_active.is_set():
                                relay_directions.add("upload")
                                upload_echo.set()
                            if download_active.is_set():
                                relay_directions.add("download")
                                download_echo.set()
                            await asyncio.sleep(0)

                    async def transfers() -> None:
                        await upload_snapshot(sftp, source, upload)
                        upload_active.clear()
                        assert remote.read_bytes() == content
                        downloaded = root / "download"
                        await download_transfer(sftp, download, downloaded)
                        download_active.clear()
                        assert downloaded.read_bytes() == content
                        with pytest.raises(FileExistsError):
                            await download_transfer(sftp, download, downloaded)
                        transfers_done.set()

                    await asyncio.gather(exercise_relay(), transfers())
                    relay.stdin.write_eof()
                    await relay.wait_closed()
                    assert relayed
                    assert relay_directions == {"upload", "download"}
                    assert 1 < peak <= 16 and outstanding == 0
                    assert 1 < read_peak <= 16 and read_outstanding == 0

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
                await close_connection(outer)
        print(f"Direct SFTP with concurrent relay and preview: {time.monotonic() - started:.3f}s")

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
            assert config.identity_file is not None
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
                asyncssh.generate_private_key("ssh-ed25519").write_private_key(wrong_identity)
                with pytest.raises(asyncssh.PermissionDenied):
                    await connect_outer(replace(config, identity_file=wrong_identity))
                assert not attempts
                assert not marker.exists()
                config.known_hosts_file.write_text("")
                with pytest.raises(asyncssh.HostKeyNotVerifiable):
                    await connect_outer(config)
                replacement = asyncssh.generate_private_key("ssh-ed25519").export_public_key()
                config.known_hosts_file.write_bytes(
                    f"[127.0.0.1]:{config.port} ".encode() + replacement
                )
                with pytest.raises(asyncssh.HostKeyNotVerifiable):
                    await connect_outer(config)
            finally:
                agent_server.close()
                await agent_server.wait_closed()

    asyncio.run(exercise())


def test_tailscale_no_challenge_handshake_and_original_identity_pins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real handshake exposes trivial-auth and numeric-address trust-union bugs."""
    import time

    from planner.executor import ssh
    from planner.executor.contracts import (
        ExecutorSshConfig,
        SshAuthenticationMode,
        ValidatedTailnetDestination,
    )

    class NoChallengeServer(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return False

    async def exercise() -> None:
        key = asyncssh.generate_private_key("ssh-ed25519")
        wrong_key = asyncssh.generate_private_key("ssh-ed25519")
        server = await asyncssh.listen(
            "127.0.0.1",
            0,
            server_factory=NoChallengeServer,
            server_host_keys=[key],
        )
        known_hosts = tmp_path / "known_hosts"
        binary = tmp_path / "tailscale"
        binary.touch(mode=0o700)
        config = ExecutorSshConfig(
            "peer.example",
            22,
            "user",
            None,
            known_hosts,
            SshAuthenticationMode.tailscale,
            binary,
        )
        original_create_connection = asyncio.get_running_loop().create_connection
        calls = []

        async def numeric_dial(
            factory: Callable[[], asyncio.Protocol],
            host: str,
            port: int,
            *,
            family: int,
            flags: int,
            local_addr: tuple[str, int] | None,
        ) -> tuple[asyncio.Transport, asyncio.Protocol]:
            assert host == "100.64.1.2" and port == 22
            calls.append((host, port))
            return await original_create_connection(
                factory,
                "127.0.0.1",
                server.get_port(),
                family=family,
                flags=flags,
                local_addr=local_addr,
            )

        async def validate(host: str, path: Path) -> ValidatedTailnetDestination:
            assert host == "peer.example" and path == binary
            return ValidatedTailnetDestination("node", "100.64.1.2")

        # Keep AsyncSSH connection, host verification, and authentication real;
        # only map the already-validated numeric socket dial to our local server.
        monkeypatch.setattr(asyncio.get_running_loop(), "create_connection", numeric_dial)
        monkeypatch.setattr(ssh, "validate_tailnet_destination", validate)
        started = time.monotonic()
        try:
            known_hosts.write_bytes(b"peer.example " + key.export_public_key())
            connection = await connect_outer(config)
            await close_connection(connection)
            for content in (
                b"",  # Unknown original host.
                b"peer.example " + wrong_key.export_public_key(),  # Changed key.
                # An address pin must never rescue the conflicting original pin.
                b"peer.example "
                + wrong_key.export_public_key()
                + b"100.64.1.2 "
                + key.export_public_key(),
                b"peer.example "
                + key.export_public_key()
                + b"@revoked peer.example "
                + key.export_public_key(),
            ):
                known_hosts.write_bytes(content)
                with pytest.raises(asyncssh.HostKeyNotVerifiable):
                    await connect_outer(config)
            assert len(calls) == 5
            print(f"Connected Tailscale handshake and pin cases: {time.monotonic() - started:.3f}s")
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())
