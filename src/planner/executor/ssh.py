"""Explicit outer SSH identity and bounded standard SFTP transfers."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncssh

from planner.executor.configuration import (
    SFTP_BLOCK_BYTES,
    SFTP_MAX_REQUESTS,
    SSH_CLOSE_TIMEOUT_SECONDS,
    SSH_CONNECT_TIMEOUT_SECONDS,
    SSH_KEEPALIVE_COUNT_MAX,
    SSH_KEEPALIVE_INTERVAL_SECONDS,
    SSH_LOGIN_TIMEOUT_SECONDS,
)
from planner.executor.contracts import (
    ExecutorSshConfig,
    FileTransfer,
    SshAuthenticationMode,
    SshConfigurationError,
)
from planner.executor.logic.ssh_configuration import validate_authentication_inputs
from planner.executor.tailscale import validate_tailnet_destination


def _client_options(
    key: asyncssh.SSHKey | None, username: str
) -> asyncssh.SSHClientConnectionOptions:
    # Passing a parsed key is essential: AsyncSSH loads adjacent *-cert.pub files
    # when given a filename, even with an explicitly empty client_certs list.
    return asyncssh.SSHClientConnectionOptions(
        config=None,
        username=username,
        client_keys=[key] if key is not None else None,
        client_certs=[],
        agent_path=None,
        agent_identities=None,
        agent_forwarding=False,
        pkcs11_provider=None,
        client_host_keys=None,
        client_host_certs=[],
        client_host_keysign=False,
        host_based_auth=False,
        public_key_auth=key is not None,
        password_auth=False,
        kbdint_auth=False,
        password=None,
        preferred_auth=["publickey"] if key is not None else [],
        disable_trivial_auth=key is not None,
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


def validate_ssh_config(config: ExecutorSshConfig) -> None:
    """Refuse incomplete or contradictory explicit connection inputs before dialing."""
    if (
        not config.host
        or config.host.startswith("-")
        or not config.username
        or not 0 < config.port < 65536
    ):
        raise SshConfigurationError(
            "SSH host, user, port, and authentication mode must be explicit"
        )
    validate_authentication_inputs(
        config.auth_mode, config.identity_file, config.tailscale_binary, config.port
    )
    paths = [config.known_hosts_file]
    if config.auth_mode is SshAuthenticationMode.public_key:
        assert config.identity_file is not None
        paths.append(config.identity_file)
    else:
        assert config.tailscale_binary is not None
        paths.append(config.tailscale_binary)
        if not os.access(config.tailscale_binary, os.X_OK):
            raise SshConfigurationError("Tailscale binary must be executable")
    if any(
        not path.is_absolute()
        or not path.is_file()
        or any(character in str(path) for character in ("\0", "\n", "\r"))
        for path in paths
    ):
        raise SshConfigurationError("SSH input paths must be absolute existing safe files")


async def connect_outer(config: ExecutorSshConfig) -> asyncssh.SSHClientConnection:
    """Connect using explicit authentication and independently pinned host identity."""
    validate_ssh_config(config)
    if config.auth_mode is SshAuthenticationMode.tailscale:
        assert config.tailscale_binary is not None
        destination = await validate_tailnet_destination(config.host, config.tailscale_binary)
        # Match only the configured identity: AsyncSSH's usual hostname/address
        # union must not allow an address-only pin to rescue a changed name pin.
        known_hosts = asyncssh.read_known_hosts(str(config.known_hosts_file)).match(
            config.host, "", None
        )
        return await asyncssh.connect(
            destination.address,
            config.port,
            options=_client_options(None, config.username),
            config=None,
            known_hosts=known_hosts,
            host_key_alias=config.host,
        )
    assert config.identity_file is not None
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


@asynccontextmanager
async def connect_sftp(
    outer: asyncssh.SSHClientConnection,
) -> AsyncIterator[asyncssh.SFTPClient]:
    """Open the account subsystem on the already authenticated connection."""
    async with outer.start_sftp_client(sftp_version=3, env={}, send_env=[]) as client:
        yield client


async def _filesystem_call[Result](function: Callable[..., Result], *args: Any) -> Result:
    """Settle admitted filesystem work before its caller releases file resources."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except BaseException:
        await asyncio.gather(task, return_exceptions=True)
        raise


async def upload_snapshot(
    client: asyncssh.SFTPClient, local_path: Path, transfer: FileTransfer
) -> None:
    """Upload exactly the manifest range and refuse a short or growing snapshot."""
    if transfer.direction != "upload" or transfer.manifest.byte_count < 0:
        raise ValueError("invalid upload transfer")
    from planner.executor.files import regular_descriptor

    with regular_descriptor(local_path) as source:
        async with client.open(
            transfer.path,
            asyncssh.FXF_WRITE,
            encoding=None,
            block_size=SFTP_BLOCK_BYTES,
            max_requests=SFTP_MAX_REQUESTS,
        ) as target:
            offset = 0
            while offset < transfer.manifest.byte_count:
                size = min(
                    SFTP_BLOCK_BYTES * SFTP_MAX_REQUESTS,
                    transfer.manifest.byte_count - offset,
                )
                content = await _filesystem_call(os.read, source, size)
                if not content:
                    raise ValueError("file shorter than manifest")
                async with asyncio.TaskGroup() as writes:
                    for start in range(0, len(content), SFTP_BLOCK_BYTES):
                        writes.create_task(
                            target.write(content[start : start + SFTP_BLOCK_BYTES], offset + start)
                        )
                offset += len(content)
            if await _filesystem_call(os.read, source, 1):
                raise ValueError("file exceeds manifest length")


async def download_transfer(
    client: asyncssh.SFTPClient, transfer: FileTransfer, local_path: Path
) -> None:
    """Download a bounded manifest range into fresh quarantine, then check EOF."""
    if transfer.direction != "download" or transfer.manifest.byte_count < 0:
        raise ValueError("invalid download transfer")
    descriptor = os.open(local_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        async with client.open(
            transfer.path,
            asyncssh.FXF_READ,
            encoding=None,
            block_size=SFTP_BLOCK_BYTES,
            max_requests=SFTP_MAX_REQUESTS,
        ) as source:
            offset = 0
            while offset < transfer.manifest.byte_count:
                ranges = [
                    (start, min(SFTP_BLOCK_BYTES, transfer.manifest.byte_count - start))
                    for start in range(
                        offset,
                        min(
                            transfer.manifest.byte_count,
                            offset + SFTP_BLOCK_BYTES * SFTP_MAX_REQUESTS,
                        ),
                        SFTP_BLOCK_BYTES,
                    )
                ]
                # TaskGroup settles all outstanding requests before the handle closes.
                async with asyncio.TaskGroup() as reads:
                    tasks = [reads.create_task(source.read(size, start)) for start, size in ranges]
                for (_, size), task in zip(ranges, tasks, strict=True):
                    content = task.result()
                    if len(content) != size:
                        raise ValueError("file shorter than manifest")
                    await _filesystem_call(target.write, content)
                    offset += size
            if await source.read(1, offset):
                raise ValueError("file exceeds manifest length")
        await _filesystem_call(target.flush)
        await _filesystem_call(os.fsync, target.fileno())
