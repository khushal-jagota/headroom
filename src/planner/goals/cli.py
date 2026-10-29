"""Goal agents and owners use the same canonical writer."""

import sys
from urllib.parse import quote

import click

from planner.cli import http
from planner.cli.artifact_io import read_artifact_source
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


@goal.group("artifact")
def artifact() -> None:
    """Read and write supporting files, with paths relative to artifacts/."""


@artifact.command("list")
@click.argument("goal_id")
@json_option
def list_artifacts(goal_id: str, as_json: bool) -> None:
    """List openable paths. Index folders hide their supporting files."""
    directories = [""]
    paths: set[str] = set()
    prefix = "artifacts/"
    while directories:
        directory = directories.pop()
        offset = 0
        while True:
            page = http.send(
                "GET",
                f"/api/artifact-catalog/goal/{quote(goal_id, safe='')}",
                params={"directory": directory, "offset": offset},
                as_json=as_json,
            )
            entries = page["entries"]
            for entry in entries:
                if entry["href"] and entry["path"].startswith(prefix):
                    paths.add(entry["path"][len(prefix) :])
                child = entry["directory"]
                if child and (child == "artifacts" or child.startswith(prefix)):
                    directories.append(child)
            offset += len(entries)
            if offset >= page["total"] or not entries:
                break
    artifacts = sorted(paths)
    http.emit({"goal_id": goal_id, "artifacts": artifacts}, as_json, "\n".join(artifacts))


def _artifact_url(goal_id: str, artifact_path: str) -> str:
    return f"/files/goals/{quote(goal_id, safe='')}/artifacts/{quote(artifact_path, safe='/')}"


@artifact.command("read")
@click.argument("goal_id")
@click.argument("artifact_path")
@json_option
def read_artifact(goal_id: str, artifact_path: str, as_json: bool) -> None:
    content = http.send_text("GET", _artifact_url(goal_id, artifact_path), as_json=as_json)
    if as_json:
        http.emit({"goal_id": goal_id, "path": artifact_path, "content": content}, True, content)
    sys.stdout.write(content)


@artifact.command("write")
@click.argument("goal_id")
@click.argument("artifact_path")
@click.option("--body-file", required=True)
@json_option
def write_artifact(goal_id: str, artifact_path: str, body_file: str, as_json: bool) -> None:
    data = http.send(
        "PUT",
        _artifact_url(goal_id, artifact_path),
        json_body={"content": read_artifact_source(body_file, as_json)},
        as_json=as_json,
    )
    http.emit(data, as_json, data["url"])


@artifact.command("delete")
@click.argument("goal_id")
@click.argument("artifact_path")
@json_option
def delete_artifact(goal_id: str, artifact_path: str, as_json: bool) -> None:
    data = http.send("DELETE", _artifact_url(goal_id, artifact_path), as_json=as_json)
    http.emit(data, as_json, f"{artifact_path} deleted")
