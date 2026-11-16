"""Owner enrollment through the existing registration authority."""

from __future__ import annotations

import click

from planner.cli import http
from planner.executor.configuration import validate_server_url
from planner.installation.join_command import join_command, require_released_revision


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
    try:
        revision = require_released_revision(metadata.get("app_sha"))
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    body = {"name": name}
    data = http.send("POST", "/api/machines", as_json=False, json_body=body)
    # Registration remains the only issuer. This command never copies owner authority.
    credential = data["credential"]
    try:
        command = join_command(
            home_url=home_url,
            revision=revision,
            machine_id=data["machine_id"],
            credential=credential,
        )
    except ValueError as error:
        raise click.ClickException(str(error)) from error
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
