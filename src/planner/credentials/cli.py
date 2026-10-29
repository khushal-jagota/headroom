"""Names-only credential discovery and owner metadata edits through the canonical API."""

from typing import Any
from urllib.parse import quote

import click

from planner.cli import http
from planner.cli.measure_io import emit, json_option, page_options


@click.group("credential")
def credential() -> None:
    """Discover credential references. Secret writes use the owner API."""


@credential.command("list")
@page_options
@json_option
def list_credentials(limit: int, offset: int, as_json: bool) -> None:
    """List reusable names and metadata without secret values."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    emit(http.send("GET", "/api/credentials", params=params, as_json=as_json), as_json)


@credential.command("rename")
@click.argument("credential_id")
@click.option("--name", required=True)
@click.option("--expected-revision", required=True, type=click.IntRange(min=1))
@json_option
def rename_credential(credential_id: str, name: str, expected_revision: int, as_json: bool) -> None:
    """Rename a reusable credential as the owner without exposing its value."""
    emit(http.send("PATCH", f"/api/credentials/{quote(credential_id, safe='')}",
                   json_body={"name": name, "expected_revision": expected_revision},
                   as_json=as_json), as_json)
