"""Authenticated JSON requests for operations without a dedicated CLI command."""

from __future__ import annotations

import json
from typing import Any, TextIO

import click

from planner.cli import http


@click.command("api", hidden=True)
@click.argument("method")
@click.argument("path")
@click.option(
    "--input-json",
    type=click.File("r", encoding="utf-8"),
    default=None,
    help="Read the JSON request body from this file, or - for stdin.",
)
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON.")
def api(method: str, path: str, input_json: TextIO | None, as_json: bool) -> None:
    """Call a Panels API path with this conversation's automatic authentication."""
    body: Any = None
    if input_json is not None:
        try:
            body = json.load(input_json)
        except json.JSONDecodeError as error:
            http.fail_validation(f"invalid request JSON: {error.msg}", as_json)
    answer = http.send(method.upper(), path, as_json=as_json, json_body=body)
    http.emit(answer, as_json, json.dumps(answer, ensure_ascii=False, indent=2))
