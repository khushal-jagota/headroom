"""Thin source operations; the server owns authority, revisions, and asynchronous runs."""

from typing import Any
from urllib.parse import quote

import click

from planner.cli import http
from planner.cli.measure_io import emit, json_option, page_options, stdin_object


@click.group("measure-source")
def measure_source() -> None:
    """Configure collection sources, save scripts, and inspect asynchronous runs."""


@measure_source.command("list")
@click.option("--project-id")
@page_options
@json_option
def list_sources(project_id: str | None, limit: int, offset: int, as_json: bool) -> None:
    """List a bounded page of sources and their collection health."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if project_id is not None:
        params["project_id"] = project_id
    emit(http.send("GET", "/api/measure-sources", params=params, as_json=as_json), as_json)


@measure_source.command("show")
@click.argument("source_id")
@json_option
def show_source(source_id: str, as_json: bool) -> None:
    """Show configuration and script revisions."""
    emit(
        http.send("GET", f"/api/measure-sources/{quote(source_id, safe='')}", as_json=as_json),
        as_json,
    )


@measure_source.command("status")
@click.argument("source_id")
@json_option
def source_status(source_id: str, as_json: bool) -> None:
    """Show collection health, its failure, and current activity."""
    emit(
        http.send(
            "GET", f"/api/measure-sources/{quote(source_id, safe='')}/status", as_json=as_json
        ),
        as_json,
    )


@measure_source.command("create")
@json_option
def create_source(as_json: bool) -> None:
    """Read {name, project_id, account, responsible} JSON from stdin."""
    emit(
        http.send("POST", "/api/measure-sources", json_body=stdin_object(as_json), as_json=as_json),
        as_json,
    )


@measure_source.command("delete")
@click.argument("source_id")
@json_option
def delete_source(source_id: str, as_json: bool) -> None:
    """Delete a source when canonical dependency checks permit it."""
    emit(
        http.send("DELETE", f"/api/measure-sources/{quote(source_id, safe='')}", as_json=as_json),
        as_json,
    )


@measure_source.command("set-schedule")
@click.argument("source_id")
@json_option
def set_schedule(source_id: str, as_json: bool) -> None:
    """Read {expected_revision, enabled, local_time} JSON from stdin."""
    emit(
        http.send(
            "PUT",
            f"/api/measure-sources/{quote(source_id, safe='')}/schedule",
            json_body=stdin_object(as_json),
            as_json=as_json,
        ),
        as_json,
    )


@measure_source.command("set-responsible")
@click.argument("source_id")
@json_option
def set_responsible(source_id: str, as_json: bool) -> None:
    """Read {expected_responsibility_revision, responsible} JSON from stdin."""
    emit(
        http.send(
            "PUT",
            f"/api/measure-sources/{quote(source_id, safe='')}/responsible",
            json_body=stdin_object(as_json),
            as_json=as_json,
        ),
        as_json,
    )


@measure_source.command("submit-script")
@click.argument("source_id")
@json_option
def submit_script(source_id: str, as_json: bool) -> None:
    """Save current script immediately from {expected_revision, text} JSON on stdin."""
    emit(
        http.send(
            "PUT",
            f"/api/measure-sources/{quote(source_id, safe='')}/script",
            json_body=stdin_object(as_json),
            as_json=as_json,
        ),
        as_json,
    )


@measure_source.command("request-run")
@click.argument("source_id")
@json_option
def request_run(source_id: str, as_json: bool) -> None:
    """Enqueue {mode: test|collection, expected_script_revision, history?} JSON from stdin."""
    emit(
        http.send(
            "POST",
            f"/api/measure-sources/{quote(source_id, safe='')}/runs",
            json_body=stdin_object(as_json),
            as_json=as_json,
        ),
        as_json,
    )


@measure_source.command("runs")
@click.argument("source_id")
@page_options
@json_option
def list_runs(source_id: str, limit: int, offset: int, as_json: bool) -> None:
    """List a bounded page of runs for one source."""
    emit(
        http.send(
            "GET",
            f"/api/measure-sources/{quote(source_id, safe='')}/runs",
            params={"limit": limit, "offset": offset},
            as_json=as_json,
        ),
        as_json,
    )


@measure_source.command("run-show")
@click.argument("run_id")
@json_option
def show_run(run_id: str, as_json: bool) -> None:
    """Show an explicit run ID, safe failure details, and test candidates."""
    emit(
        http.send("GET", f"/api/measure-source-runs/{quote(run_id, safe='')}", as_json=as_json),
        as_json,
    )
