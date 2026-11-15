"""Read historical owner involvement through the canonical attention API."""

from functools import partial
from pathlib import Path
from typing import Any

import click

from planner.attention.configuration import COLLECTION_LOCAL_TIME, DEFAULT_LIMIT, SORT_FIELDS
from planner.attention.contracts import GroupBy
from planner.cli import http
from planner.cli.measure_io import emit, json_option


@click.group("attention")
def attention() -> None:
    """Read where owner involvement went over selected dates."""


@attention.command("summary")
@click.option("--first-date", help="First inclusive local date (YYYY-MM-DD).")
@click.option("--last-date", help="Last inclusive local date (YYYY-MM-DD).")
@click.option("--group-by", type=click.Choice([item.value for item in GroupBy]), default="project")
@click.option("--project", "project_id")
@click.option("--manager", "manager_id")
@click.option("--ticket", "ticket_id")
@click.option("--goal", "goal_id")
@click.option("--chief", is_flag=True)
@click.option("--sort", type=click.Choice(SORT_FIELDS), default="owner_messages")
@click.option("--limit", type=int, default=DEFAULT_LIMIT, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@json_option
def summary(
    first_date: str | None,
    last_date: str | None,
    group_by: str,
    project_id: str | None,
    manager_id: str | None,
    ticket_id: str | None,
    goal_id: str | None,
    chief: bool,
    sort: str,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    """Compare recorded involvement with the preceding date range.

    Omit both dates for the last seven local dates. Filters select one work scope;
    --group-by selects its breakdown. These figures do not measure owner hours.
    """
    selections = [
        (kind, identifier)
        for kind, identifier in (
            ("project", project_id),
            ("manager", manager_id),
            ("ticket", ticket_id),
            ("goal", goal_id),
            ("chief", "chief" if chief else None),
        )
        if identifier is not None
    ]
    if len(selections) > 1:
        http.fail_validation("Select only one attention scope.", as_json)
    params: dict[str, Any] = {
        "group_by": group_by,
        "sort": sort,
        "limit": limit,
        "offset": offset,
    }
    if first_date is not None:
        params["first_date"] = first_date
    if last_date is not None:
        params["last_date"] = last_date
    if selections:
        params["scope_kind"], identifier = selections[0]
        if params["scope_kind"] != "chief":
            params["scope_id"] = identifier
    emit(http.send("GET", "/api/attention/summary", params=params, as_json=as_json), as_json)


@attention.command("setup-collection")
@click.option("--project", "project_id", required=True)
@click.option(
    "--database-path",
    type=click.Path(path_type=Path),
    required=True,
    help="Absolute database path on the Panels server, read-only during collection.",
)
@click.option("--local-time", default=COLLECTION_LOCAL_TIME, show_default=True)
@json_option
def setup_collection(project_id: str, database_path: Path, local_time: str, as_json: bool) -> None:
    """Configure daily retained figures through existing source and measure APIs."""
    from planner.attention.collection import setup_collection as setup

    try:
        result = setup(
            partial(http.send, as_json=as_json),
            project_id=project_id,
            database_path=database_path,
            local_time=local_time,
        )
    except ValueError as error:
        http.fail_validation(str(error), as_json)
    emit(result, as_json)
