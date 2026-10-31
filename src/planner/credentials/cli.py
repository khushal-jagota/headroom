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
    emit(
        http.send(
            "PATCH",
            f"/api/credentials/{quote(credential_id, safe='')}",
            json_body={"name": name, "expected_revision": expected_revision},
            as_json=as_json,
        ),
        as_json,
    )


@credential.command("request")
@click.option("--request-id", required=True, type=click.UUID)
@click.option("--name", required=True)
@click.option("--provider", required=True)
@click.option("--use", "use_context", required=True)
@click.option("--auth-kind", required=True, type=click.Choice(["bearer", "header"]))
@click.option("--header-name")
@click.option("--help-text", "help_hint")
@click.option("--help-url")
@click.option("--replace-id")
@click.option("--expected-revision", type=click.IntRange(min=1))
@json_option
def request_credential(
    request_id: object,
    name: str,
    provider: str,
    use_context: str,
    auth_kind: str,
    header_name: str | None,
    help_hint: str | None,
    help_url: str | None,
    replace_id: str | None,
    expected_revision: int | None,
    as_json: bool,
) -> None:
    """Request a named API key in this employee's conversation. No secret input."""
    if (replace_id is None) != (expected_revision is None):
        http.fail_validation("replacement requires --replace-id and --expected-revision", as_json)
    body = {
        "request_id": str(request_id),
        "name": name,
        "provider": provider,
        "use": use_context,
        "auth_kind": auth_kind,
        "header_name": header_name,
        "help": help_hint,
        "help_url": help_url,
        "replacement": None
        if replace_id is None
        else {"credential_id": replace_id, "name": name, "revision": expected_revision},
    }
    result = http.send("POST", "/api/credentials/requests", json_body=body, as_json=as_json)
    if as_json:
        emit(result, True)
    else:
        http.emit(result, False, f"API key request {result['state']}: {result['name']}")
