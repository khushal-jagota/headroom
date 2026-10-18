"""Explicit SSH identity and public Unix-channel bridge for restricted SFTP.

The outer account connection never opens its account-wide SFTP subsystem. The
inner connection authenticates only to the generation's restricted endpoint.
"""

from __future__ import annotations

import asyncio
import os
import socket
import stat
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncssh

from planner.executor.configuration import (
    SFTP_BLOCK_BYTES,
    SFTP_MAX_REQUESTS,
    SSH_CLOSE_TIMEOUT_SECONDS,
    SSH_CONNECT_TIMEOUT_SECONDS,
    SSH_KEEPALIVE_COUNT_MAX,
    SSH_KEEPALIVE_INTERVAL_SECONDS,
    SSH_LOGIN_TIMEOUT_SECONDS,
    SSH_UNIX_SOCKET_MAX_BYTES,
)
from planner.executor.contracts import ExecutorSshConfig, SftpEndpoint


def generate_ephemeral_key() -> asyncssh.SSHKey:
    """A generation owns this in-memory key. It must never enter durable state."""
    return asyncssh.generate_private_key("ssh-ed25519")


def _client_options(key: asyncssh.SSHKey, username: str) -> asyncssh.SSHClientConnectionOptions:
    # Passing a parsed key is essential: AsyncSSH loads adjacent *-cert.pub files
    # when given a filename, even with an explicitly empty client_certs list.
    return asyncssh.SSHClientConnectionOptions(
        config=None,
        username=username,
        client_keys=[key],
        client_certs=[],
        agent_path=None,
        agent_identities=None,
        agent_forwarding=False,
        pkcs11_provider=None,
        client_host_keys=None,
        client_host_certs=[],
        client_host_keysign=False,
        host_based_auth=False,
        public_key_auth=True,
        password_auth=False,
        kbdint_auth=False,
        password=None,
        preferred_auth=["publickey"],
        disable_trivial_auth=True,
        gss_auth=False,
        gss_kex=False,
        gss_delegate_creds=False,
        proxy_command=None,
        tunnel=None,
        canonicalize_hostname=False,
        host_key_alias=None,
        x509_trusted_certs=None,
        x509_trusted_cert_paths=[],
        x11_forwarding=False,
        request_pty=False,
        env={},
        send_env=[],
        connect_timeout=SSH_CONNECT_TIMEOUT_SECONDS,
        login_timeout=SSH_LOGIN_TIMEOUT_SECONDS,
        keepalive_interval=SSH_KEEPALIVE_INTERVAL_SECONDS,
        keepalive_count_max=SSH_KEEPALIVE_COUNT_MAX,
    )


async def connect_outer(config: ExecutorSshConfig) -> asyncssh.SSHClientConnection:
    """Connect with one explicit key and strict known_hosts. Ambient input is off."""
    if not config.host or not config.username or not 0 < config.port < 65536:
        raise ValueError("SSH hostname, username, and port must be explicit")
    for path in (config.identity_file, config.known_hosts_file):
        if not path.is_absolute() or not path.is_file():
            raise ValueError("SSH key and known_hosts must be absolute existing files")
    key = asyncssh.import_private_key(config.identity_file.read_bytes())
    return await asyncssh.connect(
        config.host,
        config.port,
        options=_client_options(key, config.username),
        config=None,
        known_hosts=str(config.known_hosts_file),
    )


async def close_connection(connection: asyncssh.SSHClientConnection) -> None:
    """Bound graceful shutdown, then abort all remaining channels."""
    connection.close()
    try:
        await asyncio.wait_for(connection.wait_closed(), SSH_CLOSE_TIMEOUT_SECONDS)
    except TimeoutError:
        connection.abort()
    except asyncio.CancelledError:
        connection.abort()
        raise


def _private_directory(path: Path) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("SSH run directory must be an absolute path without symlinks")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError("SSH run directory must be owned by the current user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("SSH run directory must have mode 0700")


@asynccontextmanager
async def connect_sftp(
    outer: asyncssh.SSHClientConnection,
    endpoint: SftpEndpoint,
    client_key: asyncssh.SSHKey,
    local_run_dir: Path,
) -> AsyncIterator[asyncssh.SFTPClient]:
    """Bridge public APIs to a pinned generation key over the authenticated outer SSH."""
    _private_directory(local_run_dir)
    if (
        not endpoint.username
        or not endpoint.socket_path.startswith("/")
        or "\0" in endpoint.socket_path
        or len(os.fsencode(endpoint.socket_path)) > SSH_UNIX_SOCKET_MAX_BYTES
    ):
        raise ValueError("Restricted SFTP endpoint must have an absolute socket and username")
    pinned_key = asyncssh.import_public_key(endpoint.server_public_key)
    listener: asyncssh.SSHListener | None = None
    connection: asyncssh.SSHClientConnection | None = None
    channel_socket: socket.socket | None = None
    # A normal macOS Application Support root can exceed sockaddr_un. The
    # generated child remains private even when its short parent is shared /tmp.
    socket_parent = local_run_dir
    if len(os.fsencode(local_run_dir)) + 15 > SSH_UNIX_SOCKET_MAX_BYTES:
        socket_parent = Path("/tmp").resolve()
    with tempfile.TemporaryDirectory(prefix="s-", dir=socket_parent) as temporary:
        local_socket = str(Path(temporary) / "s")
        # Darwin sockaddr_un allows 104 bytes including the terminating NUL.
        if len(os.fsencode(local_socket)) > SSH_UNIX_SOCKET_MAX_BYTES:
            raise ValueError("SSH Unix socket path exceeds the supported platform limit")
        try:
            listener = await asyncio.wait_for(
                outer.forward_local_path(local_socket, endpoint.socket_path),
                SSH_CONNECT_TIMEOUT_SECONDS,
            )
            channel_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            channel_socket.setblocking(False)
            await asyncio.wait_for(
                asyncio.get_running_loop().sock_connect(channel_socket, local_socket),
                SSH_CONNECT_TIMEOUT_SECONDS,
            )
            connection = await asyncssh.connect(
                sock=channel_socket,
                options=_client_options(client_key, endpoint.username),
                config=None,
                known_hosts=([pinned_key], [], []),
            )
            channel_socket = None  # The SSH connection now owns the socket.
            async with connection.start_sftp_client(sftp_version=3) as client:
                yield client
        finally:
            try:
                if connection is not None:
                    await close_connection(connection)
            finally:
                if channel_socket is not None:
                    channel_socket.close()
                if listener is not None:
                    listener.close()
                    await asyncio.wait_for(listener.wait_closed(), SSH_CLOSE_TIMEOUT_SECONDS)


async def upload_snapshot(client: asyncssh.SFTPClient, local_path: Path, token: str) -> None:
    """Stream an existing immutable snapshot into an existing server-issued slot."""
    source = await asyncio.to_thread(local_path.open, "rb")
    try:
        async with client.open(
            token,
            asyncssh.FXF_WRITE,
            encoding=None,
            block_size=SFTP_BLOCK_BYTES,
            max_requests=SFTP_MAX_REQUESTS,
        ) as target:
            offset = 0
            while content := await asyncio.to_thread(
                source.read, SFTP_BLOCK_BYTES * SFTP_MAX_REQUESTS
            ):
                async with asyncio.TaskGroup() as writes:
                    for start in range(0, len(content), SFTP_BLOCK_BYTES):
                        writes.create_task(
                            target.write(content[start : start + SFTP_BLOCK_BYTES], offset + start)
                        )
                offset += len(content)
    finally:
        await asyncio.to_thread(source.close)


async def download_slot(client: asyncssh.SFTPClient, token: str, local_path: Path) -> None:
    """Stream a read-only slot into fresh local quarantine. Validation stays separate."""
    target = await asyncio.to_thread(local_path.open, "xb")
    try:
        async with client.open(
            token,
            asyncssh.FXF_READ,
            encoding=None,
            block_size=SFTP_BLOCK_BYTES,
            max_requests=SFTP_MAX_REQUESTS,
        ) as source:
            async for offset, content in await source.read_parallel():
                await asyncio.to_thread(target.seek, offset)
                await asyncio.to_thread(target.write, content)
        await asyncio.to_thread(target.flush)
        await asyncio.to_thread(os.fsync, target.fileno())
    finally:
        await asyncio.to_thread(target.close)
