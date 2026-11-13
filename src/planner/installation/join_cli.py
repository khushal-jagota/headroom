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
@click.option("--revision", required=True, help="Full published release SHA for the bootstrap.")
@click.option("--signing-identity", help="Apple Development certificate fingerprint for a Mac.")
def machine_join_command(
    name: str,
    home_url: str,
    revision: str,
    signing_identity: str | None,
) -> None:
    """Register a machine, then print its join command and private credential once."""
    try:
        home_url = validate_server_url(home_url)
        if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError("Use the full lowercase published release SHA")
        if (
            signing_identity is not None
            and re.fullmatch(r"[0-9A-Fa-f]{40}", signing_identity) is None
        ):
            raise ValueError("Use the Apple Development certificate fingerprint")
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    data = http.send("POST", "/api/machines", as_json=False, json_body={"name": name})
    # Registration remains the only issuer. This command never copies owner authority.
    machine_id = str(UUID(data["machine_id"]))
    credential = data["credential"]
    if not isinstance(credential, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None:
        raise click.ClickException("The home returned an invalid machine credential")
    arguments = ["join", "--home", home_url, "--machine-id", machine_id]
    if signing_identity:
        arguments += ["--signing-identity", signing_identity]
    arguments += ["--revision", revision, "--github-cli"]
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
