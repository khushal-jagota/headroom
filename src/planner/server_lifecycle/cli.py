"""The `panels serve` command, importable without the rest of the CLI.

The supervisor that `serve` starts needs only the lifecycle modules; the application child
it spawns imports the server itself. Reaching `serve` through `planner.cli.main` would
import every command group first, which costs well over a second of CPU on every start,
so the installed entrypoint runs this command under a root group that holds nothing else.
`planner.cli.main` registers the same command object, so both routes parse and fail alike.
"""

from __future__ import annotations

import click

from planner.cli.errors import PanelsGroup


@click.command("serve", hidden=True)
def serve() -> None:
    """Start the web server and background planner runtime."""
    from planner.server_lifecycle.contracts import ServerLifecycleError
    from planner.server_lifecycle.supervisor import run_server_supervisor

    try:
        result = run_server_supervisor()
    except ServerLifecycleError as exc:
        raise click.ClickException(str(exc)) from exc
    if result != 0:
        raise click.ClickException("Panels application exited unexpectedly.")


def run_serve() -> None:
    """Run `panels serve` from the process arguments with the root group's error handling."""
    PanelsGroup(name="main", commands=[serve]).main()
