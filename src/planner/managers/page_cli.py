"""Read and edit Manager and Chief documents through their existing page routes."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import click

from planner.cli import http
from planner.cli.measure_io import json_option, stdin_object
from planner.managers.page_guidance import EDIT


def _route(manager_id: str | None, chief: bool, as_json: bool) -> str:
    if chief == (manager_id is not None):
        http.fail_validation(
            "Use exactly one of --manager ID or --chief.", as_json,
            recovery="panels page --help",
        )
    return "/api/chief/page" if chief else f"/api/managers/{quote(manager_id or '', safe='')}/page"


def _emit(data: Any, as_json: bool) -> None:
    http.emit(data, as_json, json.dumps(data, ensure_ascii=False, indent=2))


@click.group()
def page() -> None:
    """Read and edit the page documents that Managers and Chief can change.

    Select --manager ID or --chief. For a sole held goal's visible document,
    use goal show|edit. Start with manager workspace ID to find the visible source.
    """


@page.command("show")
@click.option("--manager", "manager_id", help="Read this Manager's own page document.")
@click.option("--chief", is_flag=True, help="Read the Chief page document.")
@json_option
def show_page(manager_id: str | None, chief: bool, as_json: bool) -> None:
    """Read the saved holder document and its page_revision."""
    _emit(http.send("GET", _route(manager_id, chief, as_json), as_json=as_json), as_json)


@page.command("edit")
@click.option("--manager", "manager_id", help="Edit this Manager's own page document.")
@click.option("--chief", is_flag=True, help="Edit the Chief page document.")
@json_option
def edit_page(manager_id: str | None, chief: bool, as_json: bool) -> None:
    """Read {page: DOCUMENT} from stdin. See page schema before a whole-document edit."""
    route = _route(manager_id, chief, as_json)
    body = stdin_object(as_json)
    if set(body) != {"page"}:
        http.fail_validation(
            "Provide exactly the page field. Use null to clear the holder document.",
            as_json, recovery="panels page schema",
        )
    _emit(http.send("PATCH", route, as_json=as_json, json_body=body), as_json)


@page.command("schema")
@json_option
def schema(as_json: bool) -> None:
    """Show the page edit envelope, shared document schema, examples, and notes."""
    _emit(EDIT.reference(), as_json)
