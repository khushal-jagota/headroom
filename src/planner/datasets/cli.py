"""Dataset definitions and retained rows through the canonical API."""

from typing import Any
from urllib.parse import quote

import click

from planner.cli import http
from planner.cli.measure_io import emit, json_option, page_options, stdin_object


@click.group("dataset")
def dataset() -> None:
    """Define datasets and read their retained dated rows."""


@dataset.command("list")
@click.option("--project-id")
@click.option("--source-id")
@page_options
@json_option
def list_datasets(
    project_id: str | None, source_id: str | None, limit: int, offset: int, as_json: bool
) -> None:
    """List a bounded page of complete definitions."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if project_id is not None:
        params["project_id"] = project_id
    if source_id is not None:
        params["source_id"] = source_id
    emit(http.send("GET", "/api/datasets", params=params, as_json=as_json), as_json)


@dataset.command("show")
@click.argument("dataset_id")
@json_option
def show_dataset(dataset_id: str, as_json: bool) -> None:
    """Show one complete definition."""
    emit(http.send("GET", f"/api/datasets/{quote(dataset_id, safe='')}", as_json=as_json), as_json)


@dataset.command("create")
@json_option
def create_dataset(as_json: bool) -> None:
    """Read the complete dataset definition as JSON from stdin."""
    emit(
        http.send("POST", "/api/datasets", json_body=stdin_object(as_json), as_json=as_json),
        as_json,
    )


@dataset.command("edit")
@click.argument("dataset_id")
@json_option
def edit_dataset(dataset_id: str, as_json: bool) -> None:
    """Read changes and expected_revision as JSON from stdin."""
    emit(
        http.send(
            "PATCH",
            f"/api/datasets/{quote(dataset_id, safe='')}",
            json_body=stdin_object(as_json),
            as_json=as_json,
        ),
        as_json,
    )


@dataset.command("read")
@json_option
def read_values(as_json: bool) -> None:
    """Read {dataset_ids, first_date, last_date, limit?} JSON from stdin."""
    emit(
        http.send("POST", "/api/datasets/read", json_body=stdin_object(as_json), as_json=as_json),
        as_json,
    )
