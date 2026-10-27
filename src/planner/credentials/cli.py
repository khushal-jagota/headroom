"""Names-only credential discovery and origin management through the canonical API."""

from typing import Any
from urllib.parse import quote

import click

from planner.cli import http
from planner.cli.measure_io import emit, json_option, page_options, stdin_object


@click.group("credential")
def credential() -> None:
    """Discover credential references and set origins. Secrets use the owner form."""


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


@credential.command("set-origins")
@click.argument("credential_id")
@json_option
def set_origins(credential_id: str, as_json: bool) -> None:
    """Read {expected_revision, allowed_origins} JSON from stdin."""
    emit(
        http.send(
            "PATCH",
            f"/api/credentials/{quote(credential_id, safe='')}",
            json_body=stdin_object(as_json),
            as_json=as_json,
        ),
        as_json,
    )
