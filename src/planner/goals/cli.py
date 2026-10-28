"""Goal agents and owners use the same canonical writer."""

from urllib.parse import quote

import click

from planner.cli import http
from planner.cli.measure_io import emit, json_option, stdin_object


@click.group("goal")
def goal() -> None:
    """Read and edit goals, their pages, and their existing measure results."""


@goal.command("list")
@click.option("--active", type=click.BOOL, default=None)
@click.option("--project-id")
@json_option
def list_goals(active: bool | None, project_id: str | None, as_json: bool) -> None:
    params = {}
    if active is not None:
        params["active"] = str(active).lower()
    if project_id is not None:
        params["project_id"] = project_id
    emit(http.send("GET", "/api/goals", params=params, as_json=as_json), as_json)


@goal.command("show")
@click.argument("goal_id")
@json_option
def show_goal(goal_id: str, as_json: bool) -> None:
    emit(http.send("GET", f"/api/goals/{quote(goal_id, safe='')}", as_json=as_json), as_json)


@goal.command("create")
@json_option
def create_goal(as_json: bool) -> None:
    """Read the goal JSON from stdin."""
    emit(http.send("POST", "/api/goals", json_body=stdin_object(as_json), as_json=as_json), as_json)


@goal.command("edit")
@click.argument("goal_id")
@json_option
def edit_goal(goal_id: str, as_json: bool) -> None:
    """Read changed facts/page and expected_revision from stdin."""
    emit(
        http.send(
            "PATCH",
            f"/api/goals/{quote(goal_id, safe='')}",
            json_body=stdin_object(as_json),
            as_json=as_json,
        ),
        as_json,
    )


@goal.command("read")
@click.argument("goal_id")
@click.option("--first-date")
@click.option("--last-date")
@json_option
def read_goal(goal_id: str, first_date: str | None, last_date: str | None, as_json: bool) -> None:
    params = {
        key: value
        for key, value in {"first_date": first_date, "last_date": last_date}.items()
        if value is not None
    }
    emit(
        http.send(
            "GET", f"/api/goals/{quote(goal_id, safe='')}/readings", params=params, as_json=as_json
        ),
        as_json,
    )
