"""Executor commands; none start a local Panels database."""

from __future__ import annotations

import asyncio
import json

import click
from pydantic import TypeAdapter

from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import executor_paths
from planner.executor.contracts import IpcOperation, QuiesceRequest
from planner.executor.ipc_client import request


@click.group("executor")
def executor_group() -> None:
    """Run the native Mac executor through the app HTTPS service."""


@executor_group.command("run")
@click.option("--executor-id", required=True)
@click.option("--application-revision", required=True)
@click.option("--panels-server-url", required=True)
def run(executor_id: str, application_revision: str, panels_server_url: str) -> None:
    from planner.environments.mac_executor.contracts import (
        ExecutorLaunchOptions,
        MacExecutorConfigurationError,
    )
    from planner.environments.mac_executor.launch_agent import validate_executor_launch
    from planner.executor.mac import MacExecutor

    try:
        validate_executor_launch(
            ExecutorLaunchOptions(executor_id, application_revision, panels_server_url),
        )
    except MacExecutorConfigurationError as error:
        raise click.ClickException(str(error)) from error
    asyncio.run(
        MacExecutor(
            paths=executor_paths(),
            revision=application_revision,
            executor_id=executor_id,
            panels_server_url=panels_server_url,
        ).run()
    )


@executor_group.command("retry")
@click.argument("submission_id")
@click.option("--json", "as_json", is_flag=True)
def retry(submission_id: str, as_json: bool) -> None:
    """Inspect the original submission. Never repeat or replace its write."""
    import httpx

    from planner.cli.http import _fail_connection, emit, executor_response, fail_validation

    try:
        response = request(IpcOperation.retry, {"submission_id": submission_id})
    except ValueError as error:
        fail_validation(str(error), as_json)
    except OSError as error:
        _fail_connection(httpx.ConnectError(str(error)), as_json)
    result = executor_response(response, as_json)
    emit(result, as_json, "The original submission returned its receipt.")


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
