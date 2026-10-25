"""Resident-facing executor commands; none start a local Panels database."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import click
from pydantic import TypeAdapter

from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION, executor_paths
from planner.executor.contracts import (
    ExecutorIdentity,
    ExecutorSshConfig,
    IpcOperation,
    QuiesceRequest,
    SshAuthenticationMode,
    SshConfigurationError,
)
from planner.executor.ipc_client import request


@click.group("executor")
def executor_group() -> None:
    """Run the Mac executor or attach its authenticated SSH relay."""


@executor_group.command("run")
@click.option("--ssh-host", required=True)
@click.option("--ssh-port", type=click.IntRange(1, 65535), required=True)
@click.option("--ssh-user", required=True)
@click.option(
    "--ssh-auth-mode",
    type=click.Choice([mode.value for mode in SshAuthenticationMode]),
    required=True,
)
@click.option("--tailscale-binary", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option(
    "--ssh-identity-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=False,
)
@click.option(
    "--ssh-known-hosts", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True
)
@click.option("--application-revision", required=True)
@click.option("--panels-server-url", required=True)
@click.option("--remote-command", default="panels executor relay", show_default=True)
def run(
    ssh_host: str,
    ssh_port: int,
    ssh_user: str,
    ssh_identity_file: Path | None,
    ssh_auth_mode: str,
    tailscale_binary: Path | None,
    ssh_known_hosts: Path,
    application_revision: str,
    panels_server_url: str,
    remote_command: str,
) -> None:
    from planner.executor.mac import MacExecutor

    if (
        not application_revision
        or len(application_revision) != 40
        or any(char not in "0123456789abcdef" for char in application_revision)
    ):
        raise click.ClickException("application revision must be the full lowercase source SHA")
    if ssh_host.startswith("-") or not ssh_host:
        raise click.ClickException("SSH host must name the resident-configured peer")
    from planner.executor.ssh import validate_ssh_config

    config = ExecutorSshConfig(
        ssh_host,
        ssh_port,
        ssh_user,
        ssh_identity_file,
        ssh_known_hosts,
        SshAuthenticationMode(ssh_auth_mode),
        tailscale_binary,
    )
    try:
        validate_ssh_config(config)
    except SshConfigurationError as error:
        raise click.ClickException(str(error)) from error
    asyncio.run(
        MacExecutor(
            paths=executor_paths(),
            revision=application_revision,
            ssh_config=config,
            remote_command=remote_command,
            panels_server_url=panels_server_url,
        ).run()
    )


@executor_group.command("relay")
@click.option("--socket-path", type=click.Path(path_type=Path), default=None)
def relay(socket_path: Path | None) -> None:
    """Relay authenticated SSH stdio to the running VPS owner's Unix socket."""
    from planner.executor.vps import vps_socket_path

    path = socket_path or vps_socket_path(os.environ.get("PLAN_DB_PATH", "data/planning.db"))

    async def run_relay() -> None:
        reader, writer = await asyncio.open_unix_connection(str(path))
        loop = asyncio.get_running_loop()
        stdin = asyncio.StreamReader()
        stdin_transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin.buffer
        )
        stdout_protocol = asyncio.streams.FlowControlMixin(loop=loop)
        stdout_transport, _ = await loop.connect_write_pipe(
            lambda: stdout_protocol, sys.stdout.buffer
        )
        stdout = asyncio.StreamWriter(stdout_transport, stdout_protocol, None, loop)

        async def inbound() -> None:
            while data := await stdin.read(65536):
                writer.write(data)
                await writer.drain()
            writer.close()

        async def outbound() -> None:
            while data := await reader.read(65536):
                stdout.write(data)
                await stdout.drain()

        tasks = (asyncio.create_task(inbound()), asyncio.create_task(outbound()))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done)
        await asyncio.gather(*pending, return_exceptions=True)
        stdin_transport.close()
        stdout_transport.close()
        writer.close()

    asyncio.run(run_relay())


@executor_group.command("health")
def health() -> None:
    response = request(IpcOperation.health, {})
    if response.error is not None:
        raise click.ClickException(str(response.error))
    click.echo(json.dumps(response.result))


@executor_group.command("quiesce")
@click.option("--expected-incarnation", required=True)
@click.option("--expected-generation", type=int, required=True)
@click.option("--expected-revision", required=True)
@click.option("--expected-pid", type=int, required=True)
@click.option("--exit-when-ready", is_flag=True)
def quiesce(
    expected_incarnation: str,
    expected_generation: int,
    expected_revision: str,
    expected_pid: int,
    exit_when_ready: bool,
) -> None:
    expected = QuiesceRequest(
        ExecutorIdentity(
            PROTOCOL_VERSION,
            expected_revision,
            SPOOL_FORMAT_VERSION,
            expected_incarnation,
            expected_generation,
        ),
        expected_pid,
        exit_when_ready,
    )
    response = request(
        IpcOperation.quiesce, TypeAdapter(QuiesceRequest).dump_python(expected, mode="json")
    )
    if response.error is not None:
        raise click.ClickException(str(response.error))
    click.echo(json.dumps(response.result))


@executor_group.command("convert-spool")
@click.option("--root", type=click.Path(path_type=Path), required=True)
def convert_spool(root: Path) -> None:
    """Convert a stopped executor's legacy spool offline. Preserve its source bytes."""
    from planner.executor.outbox_conversion import convert_legacy_spool

    try:
        convert_legacy_spool(root.expanduser().resolve() / "spool")
    except (OSError, ValueError, sqlite3.Error) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        "Spool format 2 is ready. "
        "Automatic upgrades and rollback across conversion are unsupported."
    )
