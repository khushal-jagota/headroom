"""Measure definitions and coherent value reads through the canonical API."""

from typing import Any
from urllib.parse import quote

import click

from planner.cli import http
from planner.cli.measure_io import emit, json_option, page_options, stdin_object


@click.group("measure")
def measure() -> None:
    """Define measures and read their dated values."""


@measure.command("list")
@click.option("--project-id")
@click.option("--source-id")
@page_options
@json_option
def list_measures(
    project_id: str | None, source_id: str | None, limit: int, offset: int, as_json: bool
) -> None:
    """List a bounded page of definitions."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if project_id is not None:
        params["project_id"] = project_id
    if source_id is not None:
        params["source_id"] = source_id
    emit(http.send("GET", "/api/measures", params=params, as_json=as_json), as_json)


@measure.command("show")
@click.argument("measure_id")
@json_option
def show_measure(measure_id: str, as_json: bool) -> None:
    """Show one definition."""
    emit(http.send("GET", f"/api/measures/{quote(measure_id, safe='')}", as_json=as_json), as_json)


@measure.command("create")
@json_option
def create_measure(as_json: bool) -> None:
    """Read the complete measure definition as JSON from stdin."""
    emit(
        http.send("POST", "/api/measures", json_body=stdin_object(as_json), as_json=as_json),
        as_json,
    )


@measure.command("edit")
@click.argument("measure_id")
@json_option
def edit_measure(measure_id: str, as_json: bool) -> None:
    """Read changes and expected_revision as JSON from stdin."""
    emit(
        http.send(
            "PATCH",
            f"/api/measures/{quote(measure_id, safe='')}",
            json_body=stdin_object(as_json),
            as_json=as_json,
        ),
        as_json,
    )


@measure.command("read")
@json_option
def read_values(as_json: bool) -> None:
    """Read {measure_ids, first_date, last_date, limit?} JSON from stdin."""
    emit(
        http.send("POST", "/api/measures/read", json_body=stdin_object(as_json), as_json=as_json),
        as_json,
    )


@measure.command("delete")
@click.argument("measure_id")
@json_option
def delete_measure(measure_id: str, as_json: bool) -> None:
    """Delete a definition when canonical dependency checks permit it."""
    emit(
        http.send("DELETE", f"/api/measures/{quote(measure_id, safe='')}", as_json=as_json), as_json
    )
