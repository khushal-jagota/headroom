"""Names-only credential discovery through the canonical API."""

from typing import Any

import click

from planner.cli import http
from planner.cli.measure_io import emit, json_option, page_options


@click.group("credential")
def credential() -> None:
    """Discover credential references. Secret writes use the owner API."""


@credential.command("list")
@click.option("--project-id")
@click.option("--account")
@page_options
@json_option
def list_credentials(
    project_id: str | None, account: str | None, limit: int, offset: int, as_json: bool
) -> None:
    """List names and metadata without secret values."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if project_id is not None:
        params["project_id"] = project_id
    if account is not None:
        params["account"] = account
    emit(http.send("GET", "/api/credentials", params=params, as_json=as_json), as_json)
