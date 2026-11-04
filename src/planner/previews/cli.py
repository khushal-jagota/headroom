"""One application preview for the caller's own Ticket."""

from __future__ import annotations

import os
from typing import Any

import click

from planner.cli import http


def _human(data: dict[str, Any]) -> str:
    if data.get("preview", "present") is None:
        return "No application preview is prepared."
    return "\n".join(f"{key}: {value}" for key, value in data.items() if value is not None)


@click.group()
def preview() -> None:
    """Prepare, publish, inspect, or stop your Ticket's application preview."""
    if os.environ.get("PLAN_EXECUTOR_ROOT"):
        raise click.ClickException(
            "Application previews are unavailable for Tickets running on the Mac."
        )


@preview.command()
@click.option("--json", "as_json", is_flag=True)
def prepare(as_json: bool) -> None:
    """Reserve a generation prefix before starting your application."""
    data = http.send("POST", "/api/previews/prepare", as_json=as_json)
    http.emit(data, as_json, _human(data))


@preview.command()
@click.option("--port", required=True, type=click.IntRange(1024, 65535))
@click.option("--json", "as_json", is_flag=True)
def register(port: int, as_json: bool) -> None:
    """Verify a loopback listener and return its application link."""
    data = http.send("POST", "/api/previews/register", as_json=as_json, json_body={"port": port})
    http.emit(data, as_json, _human(data))


@preview.command()
@click.option("--json", "as_json", is_flag=True)
def show(as_json: bool) -> None:
    """Show the current generation and link."""
    data = http.send("GET", "/api/previews", as_json=as_json)
    http.emit(data, as_json, _human(data))


@preview.command()
@click.option("--json", "as_json", is_flag=True)
def stop(as_json: bool) -> None:
    """End this generation and close its active requests."""
    data = http.send("DELETE", "/api/previews", as_json=as_json)
    http.emit(data, as_json, "Application preview ended.")
