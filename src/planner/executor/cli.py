"""Executor commands; none start a local Panels database."""

from __future__ import annotations

import asyncio
import json

import click

from planner.executor.configuration import executor_paths, validate_executor_launch
from planner.executor.ipc_client import request
from planner.executor.protocol import IpcOperation, QuiesceRequest, type_adapter


@click.group("executor")
def executor_group() -> None:
    """Run the native Mac executor through the app HTTPS service."""


@executor_group.command("run")
@click.option("--executor-id", required=True)
@click.option("--application-revision", required=True)
@click.option("--panels-server-url", required=True)
def run(executor_id: str, application_revision: str, panels_server_url: str) -> None:
    from planner.executor.mac_service import MacService

    try:
        validate_executor_launch(executor_id, application_revision, panels_server_url)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    asyncio.run(
        MacService(
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
@click.option("--exit-when-ready", is_flag=True)
def quiesce(exit_when_ready: bool) -> None:
    maintenance = QuiesceRequest(exit_when_ready)
    response = request(
        IpcOperation.quiesce, type_adapter(QuiesceRequest).dump_python(maintenance, mode="json")
    )
    if response.error is not None:
        raise click.ClickException(str(response.error))
    click.echo(json.dumps(response.result))
