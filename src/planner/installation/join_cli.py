"""Owner enrollment through the existing registration authority."""

from __future__ import annotations

import re
import shlex
from uuid import UUID

import click

from planner.cli import http
from planner.executor.configuration import validate_server_url


@click.command("join-command")
@click.argument("name")
@click.option(
    "--home", "home_url", required=True, help="Use this same home's trusted HTTPS origin."
)
def machine_join_command(
    name: str,
    home_url: str,
) -> None:
    """Register a machine, then print its join command and private credential once."""
    try:
        home_url = validate_server_url(home_url)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    metadata = http.send("GET", "/api/meta", as_json=False)
    revision = metadata.get("app_sha")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise click.ClickException("Install a released home before you issue a join command")
    body = {"name": name}
    data = http.send("POST", "/api/machines", as_json=False, json_body=body)
    # Registration remains the only issuer. This command never copies owner authority.
    machine_id = str(UUID(data["machine_id"]))
    credential = data["credential"]
    if not isinstance(credential, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None:
        raise click.ClickException("The home returned an invalid machine credential")
    arguments = [
        "join",
        "--home",
        home_url,
        "--machine-id",
        machine_id,
    ]
    arguments += ["--revision", revision]
    route = f"repos/khushal-jagota/headroom/contents/scripts/panels_install.py?ref={revision}"
    command = (
        "panels_join_script=$(mktemp) && gh api "
        "-H 'Accept: application/vnd.github.raw+json' "
        + shlex.quote(route)
        + ' > "$panels_join_script" && python3 "$panels_join_script" '
        + shlex.join(arguments)
    )
    click.echo(
        "Use this same home's trusted HTTPS origin. "
        "Check the address before you share the credential."
    )
    click.echo("On the new machine, use an authenticated GitHub CLI and run:")
    click.echo(command)
    click.echo("At the private credential prompt, paste this value:")
    click.echo(credential)
    click.echo("Keep this value private. Credential replacement revokes it.")
    click.echo('After the command, remove its temporary script: rm -- "$panels_join_script"')
