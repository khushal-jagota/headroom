"""The `panels` CLI.

The command tree mirrors the product model:

* day: plan and inspect a planning day
* worker-type: discover the configured Worker types
* schedule: configure exact-time creation of ordinary Tickets
* ticket: create, inspect, organize, and approve tickets
* sprint: create, inspect, edit, and populate sprints and sprint items
* worker: worker-only writes such as proposals, recaps, and notes
* chief: import work completed outside Panels
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
from click.core import ParameterSource

from planner.cli import http
from planner.cli.record_projection import (
    RecordPart,
    parse_part_names,
    part,
    project_record,
    render_text,
)
from planner.environments.cli import environment as environment_group
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.tickets.contracts import AtCap

_PRIORITIES = ["P0", "P1", "P2", "P3"]
_TICKET_ID_ENV = "PLAN_TICKET_ID"

_DAY_FIELDS = {
    "focus": "focus",
    "brief-take": "brief_take",
    "watchout": "watchout",
    "if-today-lands": "if_today_lands",
    "midday-reconciliation": "midday_reconciliation",
    "notes": "notes",
}

_TICKET_SET_FIELDS = {
    "title": "title",
    "kickoff-note": "kickoff",
    "priority": "priority",
    "deadline": "deadline",
    "project": "project",
    "project-id": "project_id",
}

_SPRINT_FIELDS = {
    "name": "name",
    "date-start": "date_start",
    "date-end": "date_end",
    "limiting-factor": "limiting_factor",
    "primary-bet": "primary_bet",
    "supports": "supports",
    "premortem": "premortem",
    "mid-where-we-stand": "mid_where_we_stand",
    "mid-whats-changed": "mid_whats_changed",
    "mid-what-to-adjust": "mid_what_to_adjust",
    "outcomes": "outcomes",
    "solo-reflection": "solo_reflection",
    "joint-discussion": "joint_discussion",
    "updates-to-thinking": "updates_to_thinking",
    "carry-forward": "carry_forward",
}

_ITEM_FIELDS = {
    "title": "title",
    "body": "body",
    "priority": "priority",
    "deadline": "deadline",
    "project": "project",
    "project-id": "project_id",
    "sprint": "sprint_id",
}

_PROJECT_FIELDS = {
    "name": "name",
    "priority": "priority",
    "summary": "summary",
}

_SCHEDULE_FIELDS = {
    "enabled": "enabled",
    "cadence": "cadence",
    "time": "local_time",
    "title": "title",
    "worker-type": "worker_type",
    "kickoff-note": "kickoff_note",
    "priority": "priority",
    "deadline": "deadline",
    "project-id": "project_id",
    "placement": "placement_mode",
    "sprint-item": "sprint_item_id",
    "employee-backend": "employee_backend",
    "employee-launch-model": "employee_launch_model",
}

_SCHEDULE_CADENCES = (
    "every-planning-day",
    "current-sprint-day-four",
    "current-sprint-final-day",
)


def _read_source(spec: str, as_json: bool) -> str:
    if spec == "-":
        return sys.stdin.read()
    try:
        return Path(spec).read_text(encoding="utf-8")
    except OSError:
        http.fail_validation(f"cannot read body file: {spec}", as_json)


def _positional_is_stdin_marker(positional: str | None) -> bool:
    if positional != "-":
        return False
    source = click.get_current_context().get_parameter_source("ticket_id")
    return source == ParameterSource.COMMANDLINE


def read_body(
    positional_ticket_id: str | None, body_file: str | None, as_json: bool
) -> str:
    if body_file is not None:
        text = _read_source(body_file, as_json)
    elif _positional_is_stdin_marker(positional_ticket_id):
        text = sys.stdin.read()
    else:
        http.fail_validation(
            "body required: pass '-' for stdin or --body-file PATH", as_json
        )
    if not text.strip():
        http.fail_validation("empty body", as_json)
    return text


def read_optional_body(body_file: str | None, as_json: bool) -> str | None:
    if body_file is None:
        return None
    return _read_source(body_file, as_json)


def read_required_option_body(body_file: str | None, as_json: bool, label: str) -> str:
    if body_file is None:
        http.fail_validation(f"{label} required: pass --{label}-file PATH", as_json)
    text = _read_source(body_file, as_json)
    if not text.strip():
        http.fail_validation(f"empty {label}", as_json)
    return text


def read_value_or_file(
    value: str | None,
    body_file: str | None,
    clear: bool,
    as_json: bool,
    field_label: str,
) -> str | None:
    sources = sum(
        1 for present in (value is not None, body_file is not None, clear) if present
    )
    if sources != 1:
        http.fail_validation(
            f"set {field_label} with exactly one of --value, --body-file, or --clear",
            as_json,
        )
    if clear:
        return None
    if body_file is not None:
        return _read_source(body_file, as_json)
    return value


def read_recap(recap: str | None, recap_file: str | None, as_json: bool) -> str:
    if recap is not None and recap_file is not None:
        http.fail_validation(
            "recap accepts only one of --recap or --recap-file", as_json
        )
    if recap_file is not None:
        text = _read_source(recap_file, as_json)
    elif recap is not None:
        text = recap
    else:
        http.fail_validation(
            "recap required: pass --recap TEXT or --recap-file PATH", as_json
        )
    if not text.strip():
        http.fail_validation("empty recap", as_json)
    return text


def resolve_ticket_id(positional: str | None, as_json: bool) -> str:
    if positional is not None and positional != "-":
        return positional
    env = os.environ.get(_TICKET_ID_ENV, "").strip()
    if env and env != "-":
        return env
    http.fail_validation("ticket id required: pass it or set PLAN_TICKET_ID", as_json)


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _lines(rows: list[Any], fmt: Callable[[Any], str]) -> str:
    return "\n".join(fmt(r) for r in rows) if rows else "(none)"


def _current_sprint_id(as_json: bool) -> str:
    data = http.send(
        "GET", "/api/sprint/current", as_json=as_json, request_actor="ordinary"
    )
    sprint = data.get("sprint") if isinstance(data, dict) else None
    if sprint is None:
        http.fail_validation("no current sprint", as_json)
    return str(sprint["id"])


def _record_parts(raw: str | None, as_json: bool) -> tuple[str, ...] | None:
    try:
        return parse_part_names(raw)
    except ValueError as exc:
        http.fail_validation(str(exc), as_json)


def _emit_record(
    header: dict[str, Any],
    parts: dict[str, RecordPart],
    raw_part_names: str | None,
    as_json: bool,
) -> None:
    try:
        projection = project_record(header, parts, _record_parts(raw_part_names, as_json))
    except ValueError as exc:
        http.fail_validation(str(exc), as_json)
    http.emit(projection, as_json, render_text(projection))


def _ticket_record(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    header_keys = (
        "id",
        "title",
        "worker_type",
        "worker",
        "employee_backend",
        "employee_launch_model",
        "employee_launch_reasoning_effort",
        "stage",
        "ticket_status",
        "priority",
        "deadline",
        "project_id",
        "sprint_item_id",
        "effective_sprint_id",
        "default_stage_ownership_mode",
        "effective_stage_ownership_mode",
        "ceiling",
        "at_cap",
    )
    header = {key: data[key] for key in header_keys if key in data}
    parts = {
        name: part(
            field.get("value"),
            user_note=field.get("user_note"),
            proposal=field.get("proposal"),
        )
        for name, field in data["fields"].items()
    }
    return header, parts


def _sprint_record(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    header = {key: data[key] for key in ("id", "name", "date_start", "date_end")}
    part_names = (
        "limiting_factor",
        "primary_bet",
        "supports",
        "premortem",
        "mid_where_we_stand",
        "mid_whats_changed",
        "mid_what_to_adjust",
        "outcomes",
        "solo_reflection",
        "joint_discussion",
        "updates_to_thinking",
        "carry_forward",
    )
    return header, {name: part(data[name]) for name in part_names}


def _sprint_item_record(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    header_keys = (
        "id",
        "title",
        "status",
        "sprint_id",
        "project_id",
        "project",
        "priority",
        "deadline",
        "kind",
        "blocked_by",
        "rollup",
        "blockers_cleared",
    )
    return (
        {key: data[key] for key in header_keys if key in data},
        {"body": part(data["body"])},
    )


def _day_record(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    part_names = (
        "focus",
        "brief_take",
        "watchout",
        "if_today_lands",
        "midday_reconciliation",
        "notes",
    )
    return {"id": data["id"]}, {name: part(data[name]) for name in part_names}


def _project_record(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    return (
        {key: data[key] for key in ("id", "name", "priority")},
        {"summary": part(data["summary"])},
    )


# The manifest arrives as a plain JSON body across the HTTP boundary, so the CLI holds
# it as a plain dict. Keeping the transport shape local leaves CLI request construction
# independent of Worker-type interpretation.
def _worker_types(as_json: bool) -> dict[str, dict[str, Any]]:
    """The served manifests keyed by Worker type — the single source for Stage order /
    gates / fields / ceiling range per type (no parallel lifecycle encoding in the CLI).
    """
    data = http.send(
        "GET", "/api/worker-types", as_json=as_json, request_actor="ordinary"
    )
    return {m["worker_type"]: m for m in data["worker_types"]}


def _worker_type(worker_type: str, as_json: bool) -> dict[str, Any]:
    worker_types = _worker_types(as_json)
    if worker_type not in worker_types:
        http.fail_validation(
            f"unknown worker type: {worker_type} (known: {', '.join(sorted(worker_types))})",
            as_json,
        )
    return worker_types[worker_type]


def _gating_field_for_stage(manifest: dict[str, Any], stage: str) -> str | None:
    """The field the type's stage gates (None for a terminal). Driven off the ticket's
    own Worker type manifest, not a global gate map."""
    for candidate in manifest["stages"]:
        if candidate["id"] == stage:
            gating = candidate["gating_field"]
            return str(gating) if gating is not None else None
    return None


def sprint_value_for_write(raw: str | None, as_json: bool) -> str | None:
    if raw is None:
        return None
    if raw == "none":
        return None
    if raw == "current":
        return _current_sprint_id(as_json)
    return raw


def sprint_value_for_filter(raw: str | None, as_json: bool) -> str | None:
    if raw is None:
        return None
    if raw == "none":
        return "null"
    if raw == "current":
        return _current_sprint_id(as_json)
    return raw


def _format_tickets(tickets: list[dict[str, Any]]) -> str:
    return _lines(
        tickets,
        lambda t: f"{t['id']} {t['stage']} {t['priority']} {t['title']}",
    )


def _format_ticket_summaries(tickets: list[dict[str, Any]]) -> str:
    return _lines(
        tickets,
        lambda ticket: (
            f"{ticket['id']} {ticket['stage']} {ticket['ticket_status']} "
            f"{ticket['priority']} {ticket['title']}"
            + (f" | {ticket['project']}" if ticket["project"] else "")
            + (f"\n  {ticket['recap_preview']}" if ticket["recap_preview"] else "")
        ),
    )


def _format_bounded(rows: str, page: dict[str, Any]) -> str:
    if page["match_count"] == 0:
        result = "No matches."
    elif page["return_count"] == 0:
        result = "No results at this offset."
    else:
        result = rows
    facts = (
        f"Returned {page['return_count']} of {page['match_count']} matches. "
        f"Omitted {page['omitted_before']} before and {page['omitted_after']} after. "
        f"Complete: {'yes' if page['complete'] else 'no'}."
    )
    directions: list[str] = []
    if page["omitted_before"]:
        directions.append("Use --offset 0 to start from the first match.")
    if page["next_offset"] is not None:
        directions.append(
            f"Use --limit {page['limit']} --offset {page['next_offset']} for the next page."
        )
    return "\n".join((result, facts, *directions))


def bounded_options(func: Callable[..., Any]) -> Callable[..., Any]:
    func = click.option(
        "--offset", type=click.IntRange(min=0), default=0, show_default=True
    )(func)
    return click.option(
        "--limit",
        type=click.IntRange(min=1),
        default=DEFAULT_LIST_LIMIT,
        show_default=True,
    )(func)


def _format_day(data: dict[str, Any]) -> str:
    lines = [
        f"day {data['id']}",
        f"focus: {data['focus'] or ''}",
        f"brief-take: {data['brief_take'] or ''}",
        f"watchout: {data['watchout'] or ''}",
        f"if-today-lands: {data['if_today_lands'] or ''}",
        f"midday-reconciliation: {data['midday_reconciliation'] or ''}",
        "tickets:",
        _format_tickets(data["tickets"]),
    ]
    return "\n".join(lines)


def add_project_selectors(
    body: dict[str, Any],
    *,
    project: str | None,
    project_id: str | None,
    required: bool,
    as_json: bool,
) -> None:
    if required and project is None and project_id is None:
        http.fail_validation(
            "project required: pass --project or --project-id", as_json
        )
    if project is not None:
        body["project"] = project
    if project_id is not None:
        body["project_id"] = project_id


def json_option(func: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--json",
        "as_json",
        is_flag=True,
        default=False,
        help="Print machine-readable JSON. Errors are printed as JSON on stderr.",
    )(func)


@click.group()
def main() -> None:
    """Operate the local planner server."""


main.add_command(environment_group)


@main.command("serve")
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


@main.command("restart")
def restart() -> None:
    """Ask the foreground Panels supervisor to replace its application child."""
    from planner.core.config import load_config
    from planner.server_lifecycle.contracts import (
        ServerRestartConnectionError,
        ServerRestartProtocolError,
    )
    from planner.server_lifecycle.control import (
        request_server_restart,
        resolve_server_control_socket_path,
    )
    from planner.server_lifecycle.supervisor import resolve_planner_launch_root

    launch_root = resolve_planner_launch_root()
    config = load_config(
        os.environ.get("PLAN_CONFIG_PATH", str(launch_root / "config.yaml"))
    )
    control_socket_path = resolve_server_control_socket_path(
        config.port,
        os.environ,
        launch_root,
    )
    try:
        request_server_restart(control_socket_path)
    except (ServerRestartConnectionError, ServerRestartProtocolError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("Panels restart accepted.")


# --- schedule -----------------------------------------------------------------


@main.group("schedule")
def schedule_group() -> None:
    """Configure exact-time creation of ordinary Tickets."""


@schedule_group.command("create")
@click.option("--title", required=True, help="Title for each created Ticket.")
@click.option(
    "--worker-type",
    "worker_type",
    required=True,
    help="Registered Worker type id. List choices with `panels worker-type list`.",
)
@click.option(
    "--time", "local_time", required=True, help="Exact local time in HH:MM form."
)
@click.option(
    "--cadence",
    type=click.Choice(_SCHEDULE_CADENCES),
    default="every-planning-day",
    show_default=True,
)
@click.option(
    "--enabled/--disabled", default=True, help="Whether the schedule may run."
)
@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
@click.option("--deadline", default=None, help="Ticket deadline in YYYY-MM-DD form.")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--sprint", "sprint_id", default=None, help="Fixed Sprint id or current.")
@click.option(
    "--sprint-item", "sprint_item", default=None, help="Parent sprint item id."
)
@click.option(
    "--backlog", is_flag=True, default=False, help="Keep created Tickets in backlog."
)
@click.option(
    "--employee-backend", default=None, help="Registered employee backend override."
)
@click.option(
    "--employee-launch-model", default=None, help="Model for an overriding backend."
)
@click.option(
    "--blocked-by",
    "blocked_by_ticket_ids",
    multiple=True,
    help="Existing blocking Ticket id (repeatable).",
)
@click.option("--kickoff-note", default=None, help="Proposed kickoff context.")
@click.option(
    "--kickoff-note-file", default=None, help="Read kickoff context from this file."
)
@json_option
def schedule_create(
    title: str,
    worker_type: str,
    local_time: str,
    cadence: str,
    enabled: bool,
    priority: str | None,
    deadline: str | None,
    project: str | None,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item: str | None,
    backlog: bool,
    employee_backend: str | None,
    employee_launch_model: str | None,
    blocked_by_ticket_ids: tuple[str, ...],
    kickoff_note: str | None,
    kickoff_note_file: str | None,
    as_json: bool,
) -> None:
    if kickoff_note is not None and kickoff_note_file is not None:
        http.fail_validation("kickoff note accepts only one note option", as_json)
    if backlog and (sprint_item is not None or sprint_id is not None):
        http.fail_validation(
            "--backlog cannot be combined with --sprint or --sprint-item", as_json
        )
    body: dict[str, Any] = {
        "title": title,
        "worker_type": worker_type,
        "local_time": local_time,
        "cadence": cadence.replace("-", "_"),
        "enabled": enabled,
    }
    if kickoff_note_file is not None:
        body["kickoff_note"] = _read_source(kickoff_note_file, as_json)
    elif kickoff_note is not None:
        body["kickoff_note"] = kickoff_note
    if priority is not None:
        body["priority"] = priority
    if deadline is not None:
        body["deadline"] = deadline
    add_project_selectors(
        body, project=project, project_id=project_id, required=False, as_json=as_json
    )
    if sprint_id is not None:
        body["sprint_id"] = sprint_value_for_write(sprint_id, as_json)
    if sprint_item is not None:
        body["sprint_item_id"] = sprint_item
    elif backlog:
        body["sprint_item_id"] = None
    if employee_backend is not None:
        body["employee_backend"] = employee_backend
    if employee_launch_model is not None:
        body["employee_launch_model"] = employee_launch_model
    if blocked_by_ticket_ids:
        body["blocked_by_ticket_ids"] = list(blocked_by_ticket_ids)
    result = http.send(
        "POST",
        "/api/schedules",
        as_json=as_json,
        json_body=body,
        request_actor="ordinary",
    )
    http.emit(
        result,
        as_json,
        f"{result['id']} {result['local_time']} {result['cadence']} {result['worker_type']}",
    )


@schedule_group.command("list")
@json_option
def schedule_list(as_json: bool) -> None:
    result = http.send(
        "GET", "/api/schedules", as_json=as_json, request_actor="ordinary"
    )
    http.emit(
        result,
        as_json,
        _lines(
            result["schedules"],
            lambda item: (
                f"{item['id']} {'enabled' if item['enabled'] else 'disabled'} "
                f"{item['local_time']} {item['cadence']} {item['worker_type']} {item['title']}"
            ),
        ),
    )


@schedule_group.command("show")
@click.argument("schedule_id")
@json_option
def schedule_show(schedule_id: str, as_json: bool) -> None:
    result = http.send(
        "GET",
        f"/api/schedules/{schedule_id}",
        as_json=as_json,
        request_actor="ordinary",
    )
    occurrences = result.get("occurrences", [])
    latest = "never run"
    if occurrences:
        latest_item = occurrences[0]
        latest = f"{latest_item['outcome']} {latest_item['occurrence_key']}"
    text = (
        f"{result['id']} {'enabled' if result['enabled'] else 'disabled'} "
        f"{result['local_time']} {result['cadence']} {result['worker_type']} "
        f"{result['title']} · {latest}"
    )
    http.emit(result, as_json, text)


@schedule_group.command("set")
@click.argument("schedule_id")
@click.argument("field", type=click.Choice(sorted(_SCHEDULE_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option("--body-file", default=None, help="Read field text from this file, or -.")
@click.option("--clear", is_flag=True, default=False, help="Clear a nullable field.")
@json_option
def schedule_set(
    schedule_id: str,
    field: str,
    value: str | None,
    body_file: str | None,
    clear: bool,
    as_json: bool,
) -> None:
    api_field = _SCHEDULE_FIELDS[field]
    new_value = read_value_or_file(value, body_file, clear, as_json, field)
    if (
        field
        in {
            "cadence",
            "time",
            "title",
            "worker-type",
            "kickoff-note",
            "priority",
            "enabled",
            "placement",
        }
        and new_value is None
    ):
        http.fail_validation(f"{field} cannot be cleared", as_json)
    if field == "enabled":
        lowered = str(new_value).lower()
        if lowered not in {"true", "false"}:
            http.fail_validation("enabled must be true or false", as_json)
        body_value: object = lowered == "true"
    elif field == "cadence":
        if new_value not in _SCHEDULE_CADENCES:
            http.fail_validation(
                f"cadence must be one of: {', '.join(_SCHEDULE_CADENCES)}", as_json
            )
        body_value = new_value.replace("-", "_")
    elif field == "priority":
        if new_value not in _PRIORITIES:
            http.fail_validation("priority must be P0, P1, P2, or P3", as_json)
        body_value = new_value
    elif field == "placement":
        if new_value not in {"current-sprint", "backlog"}:
            http.fail_validation("placement must be current-sprint or backlog", as_json)
        body_value = str(new_value).replace("-", "_")
    elif field == "sprint" and new_value is not None:
        body_value = sprint_value_for_write(new_value, as_json)
    else:
        body_value = new_value
    result = http.send(
        "PATCH",
        f"/api/schedules/{schedule_id}",
        as_json=as_json,
        json_body={api_field: body_value},
        request_actor="ordinary",
    )
    http.emit(result, as_json, f"{result['id']} {field} set")


# --- worker types -------------------------------------------------------------


@main.group("worker-type")
def worker_type_group() -> None:
    """Discover the configured Worker types."""


@worker_type_group.command("list")
@json_option
def worker_type_list(as_json: bool) -> None:
    data = http.send(
        "GET", "/api/worker-types", as_json=as_json, request_actor="ordinary"
    )
    http.emit(
        data,
        as_json,
        _lines(data["worker_types"], lambda item: str(item["worker_type"])),
    )


# --- project ------------------------------------------------------------------


@main.group("project")
def project_group() -> None:
    """List, inspect, create, and update projects."""


@project_group.command("list")
@bounded_options
@json_option
def project_list(limit: int, offset: int, as_json: bool) -> None:
    data = http.send(
        "GET",
        "/api/project-summaries",
        as_json=as_json,
        params={"limit": limit, "offset": offset},
        request_actor="ordinary",
    )
    http.emit(
        data,
        as_json,
        _format_bounded(
            _lines(data["projects"], lambda p: f"{p['id']} {p['name']}"),
            data["page"],
        ),
    )


@project_group.command("show")
@click.argument("project_id")
@click.argument("part_names", required=False)
@json_option
def project_show(project_id: str, part_names: str | None, as_json: bool) -> None:
    """Show a Project manifest or selected comma-separated PART_NAMES."""
    data = http.send(
        "GET",
        f"/api/projects/{project_id}",
        as_json=as_json,
        request_actor="ordinary",
    )
    header, parts = _project_record(data)
    _emit_record(header, parts, part_names, as_json)


@project_group.command("create")
@click.option("--name", required=True, help="Project display name.")
@click.option(
    "--priority",
    type=click.Choice(_PRIORITIES),
    required=True,
    help="Assessed Project priority.",
)
@click.option("--summary", default=None, help="Optional project summary text.")
@json_option
def project_create(name: str, priority: str, summary: str | None, as_json: bool) -> None:
    body: dict[str, Any] = {"name": name, "priority": priority}
    if summary is not None:
        body["summary"] = summary
    data = http.send(
        "POST",
        "/api/projects",
        as_json=as_json,
        json_body=body,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} {data['name']}")


@project_group.command("set")
@click.argument("project_id")
@click.argument("field", type=click.Choice(sorted(_PROJECT_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option(
    "--body-file", default=None, help="Read field text from this file, or - for stdin."
)
@click.option(
    "--clear", is_flag=True, default=False, help="Set the field to empty text."
)
@json_option
def project_set(
    project_id: str,
    field: str,
    value: str | None,
    body_file: str | None,
    clear: bool,
    as_json: bool,
) -> None:
    api_field = _PROJECT_FIELDS[field]
    new_value = read_value_or_file(value, body_file, clear, as_json, field)
    if field in {"name", "priority"} and new_value is None:
        http.fail_validation(f"{field} cannot be cleared", as_json)
    if field == "priority" and new_value not in _PRIORITIES:
        http.fail_validation("priority must be P0, P1, P2, or P3", as_json)
    data = http.send(
        "PATCH",
        f"/api/projects/{project_id}",
        as_json=as_json,
        json_body={api_field: "" if new_value is None else new_value},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


# --- day ----------------------------------------------------------------------


@main.group("day")
def day() -> None:
    """Plan and inspect a day."""


@day.command("show")
@click.argument("date", required=False)
@click.argument("part_names", required=False)
@click.option("--date", "date_option", default=None, help="today or YYYY-MM-DD.")
@json_option
def day_show(
    date: str | None,
    part_names: str | None,
    date_option: str | None,
    as_json: bool,
) -> None:
    """Show a Day manifest or selected comma-separated PART_NAMES."""
    if date_option is not None and date is not None:
        if part_names is not None:
            http.fail_validation(
                "with --date, pass only the optional part list positionally", as_json
            )
        if date == date_option:
            date_ = date_option
        else:
            date_, part_names = date_option, date
    else:
        date_ = date or date_option or "today"
    data = http.send(
        "GET", f"/api/day/{date_}", as_json=as_json, request_actor="ordinary"
    )
    header, parts = _day_record(data)
    _emit_record(header, parts, part_names, as_json)


@day.command("list-tickets")
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@bounded_options
@json_option
def day_list_tickets(date_: str, limit: int, offset: int, as_json: bool) -> None:
    data = http.send(
        "GET",
        f"/api/day/{date_}/tickets",
        as_json=as_json,
        params={"limit": limit, "offset": offset},
        request_actor="ordinary",
    )
    http.emit(
        data,
        as_json,
        _format_bounded(_format_ticket_summaries(data["tickets"]), data["page"]),
    )


@day.command("set")
@click.argument("field", type=click.Choice(sorted(_DAY_FIELDS)))
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@click.option("--value", default=None, help="Set the field to this text.")
@click.option(
    "--body-file", default=None, help="Read field text from this file, or - for stdin."
)
@click.option(
    "--clear", is_flag=True, default=False, help="Set the field to empty text."
)
@json_option
def day_set(
    field: str,
    date_: str,
    value: str | None,
    body_file: str | None,
    clear: bool,
    as_json: bool,
) -> None:
    api_field = _DAY_FIELDS[field]
    new_value = read_value_or_file(value, body_file, clear, as_json, field)
    data = http.send(
        "PATCH",
        f"/api/day/{date_}",
        as_json=as_json,
        json_body={api_field: "" if new_value is None else new_value},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"day {data['id']} {field} set")


@day.command("add-ticket")
@click.argument("ticket_id")
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@json_option
def day_add_ticket(ticket_id: str, date_: str, as_json: bool) -> None:
    data = http.send(
        "POST",
        f"/api/day/{date_}/tickets",
        as_json=as_json,
        json_body={"ticket_id": ticket_id},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"day {data['id']}: {len(data['tickets'])} ticket(s)")


@day.command("remove-ticket")
@click.argument("ticket_id")
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@json_option
def day_remove_ticket(ticket_id: str, date_: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        f"/api/day/{date_}/tickets/{ticket_id}",
        as_json=as_json,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"day {data['id']}: {len(data['tickets'])} ticket(s)")


# --- ticket -------------------------------------------------------------------


@main.group("ticket")
def ticket() -> None:
    """Create, inspect, organize, and approve tickets."""


@ticket.command("create")
@click.option("--title", required=True, help="Ticket title.")
@click.option(
    "--worker-type",
    "worker_type",
    required=True,
    help="Registered Worker type id. List choices with `panels worker-type list`.",
)
@click.option(
    "--employee-backend", default=None, help="Registered employee backend override."
)
@click.option(
    "--employee-launch-model",
    default=None,
    help="Model for an overriding backend; required when it is not the Worker type's own.",
)
@click.option(
    "--priority", type=click.Choice(_PRIORITIES), default=None, help="Priority label."
)
@click.option("--deadline", default=None, help="Due date in YYYY-MM-DD form.")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--sprint", "sprint_id", default=None, help="Sprint id, or current.")
@click.option(
    "--sprint-item", "sprint_item", default=None, help="Parent sprint item id."
)
@click.option(
    "--backlog", is_flag=True, default=False, help="Leave the Ticket unparented."
)
@click.option(
    "--blocked-by",
    "blocked_by_ticket_ids",
    multiple=True,
    help="Existing blocking Ticket id (repeatable).",
)
@click.option(
    "--kickoff-note", default=None, help="Proposed intake context / user guidance."
)
@click.option(
    "--kickoff-note-file",
    default=None,
    help="Read proposed kickoff note from this file, or -.",
)
@json_option
def ticket_create(
    title: str,
    worker_type: str,
    employee_backend: str | None,
    employee_launch_model: str | None,
    priority: str | None,
    deadline: str | None,
    project: str | None,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item: str | None,
    backlog: bool,
    blocked_by_ticket_ids: tuple[str, ...],
    kickoff_note: str | None,
    kickoff_note_file: str | None,
    as_json: bool,
) -> None:
    body: dict[str, Any] = {"title": title, "worker_type": worker_type}
    if employee_backend is not None:
        body["employee_backend"] = employee_backend
    if employee_launch_model is not None:
        body["employee_launch_model"] = employee_launch_model
    if kickoff_note is not None and kickoff_note_file is not None:
        http.fail_validation("kickoff note accepts only one note option", as_json)
    if kickoff_note_file is not None:
        body["kickoff_note"] = _read_source(kickoff_note_file, as_json)
    elif kickoff_note is not None:
        body["kickoff_note"] = kickoff_note
    if priority is not None:
        body["priority"] = priority
    if deadline is not None:
        body["deadline"] = deadline
    add_project_selectors(
        body, project=project, project_id=project_id, required=False, as_json=as_json
    )
    if sprint_id is not None:
        body["sprint_id"] = sprint_value_for_write(sprint_id, as_json)
    if backlog and (sprint_item is not None or sprint_id is not None):
        http.fail_validation(
            "--backlog cannot be combined with --sprint or --sprint-item", as_json
        )
    if sprint_item is not None:
        body["sprint_item_id"] = sprint_item
    elif backlog:
        body["sprint_item_id"] = None
    if blocked_by_ticket_ids:
        body["blocked_by_ticket_ids"] = list(blocked_by_ticket_ids)
    data = http.send(
        "POST",
        "/api/tickets",
        as_json=as_json,
        json_body=body,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} {data['stage']}")


@ticket.command("show")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.argument("part_names", required=False)
@json_option
def ticket_show(ticket_id: str | None, part_names: str | None, as_json: bool) -> None:
    """Show a Ticket manifest or selected comma-separated PART_NAMES."""
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "GET", f"/api/tickets/{tid}", as_json=as_json, request_actor="ordinary"
    )
    header, parts = _ticket_record(data)
    _emit_record(header, parts, part_names, as_json)


@ticket.command("delete")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Permanently delete the ticket and all of its working history.",
)
@json_option
def ticket_delete(ticket_id: str | None, yes: bool, as_json: bool) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    if not yes:
        http.fail_validation("permanent deletion requires --yes", as_json)
    data = http.send(
        "DELETE", f"/api/tickets/{tid}", as_json=as_json, request_actor="ordinary"
    )
    http.emit(data, as_json, f"{tid} permanently deleted")


@ticket.command("list")
@click.option("--stage", multiple=True, help="Include this Stage. Repeat for more.")
@click.option(
    "--exclude-stage", multiple=True, help="Exclude this Stage. Repeat for more."
)
@click.option(
    "--ticket-status",
    multiple=True,
    help="Include this ticket status. Repeat for more.",
)
@click.option(
    "--exclude-ticket-status",
    multiple=True,
    help="Exclude this ticket status. Repeat for more.",
)
@click.option(
    "--include-terminal",
    is_flag=True,
    default=False,
    help="Include done and dropped Tickets.",
)
@click.option("--search", default=None, help="Case-insensitive Ticket text search.")
@click.option("--project", default=None, help="Only show project name.")
@click.option("--project-id", default=None, help="Only show project id.")
@click.option("--sprint", default=None, help="Sprint id, current, or none.")
@click.option(
    "--sprint-item", "sprint_item", default=None, help="Only show tickets in this item."
)
@click.option("--day", default=None, help="today or YYYY-MM-DD.")
@bounded_options
@json_option
def ticket_list(
    stage: tuple[str, ...],
    exclude_stage: tuple[str, ...],
    ticket_status: tuple[str, ...],
    exclude_ticket_status: tuple[str, ...],
    include_terminal: bool,
    search: str | None,
    project: str | None,
    project_id: str | None,
    sprint: str | None,
    sprint_item: str | None,
    day: str | None,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    params = _drop_none(
        {
            "stage": list(stage) or None,
            "exclude_stage": list(exclude_stage) or None,
            "ticket_status": list(ticket_status) or None,
            "exclude_ticket_status": list(exclude_ticket_status) or None,
            "include_terminal": include_terminal or None,
            "search": search,
            "project": project,
            "project_id": project_id,
            "sprint_id": sprint_value_for_filter(sprint, as_json),
            "sprint_item_id": sprint_item,
            "day": day,
            "limit": limit,
            "offset": offset,
        }
    )
    data = http.send(
        "GET",
        "/api/ticket-summaries",
        as_json=as_json,
        params=params,
        request_actor="ordinary",
    )
    http.emit(
        data,
        as_json,
        _format_bounded(_format_ticket_summaries(data["tickets"]), data["page"]),
    )


@ticket.command("set")
@click.argument("ticket_id")
@click.argument("field", type=click.Choice(sorted(_TICKET_SET_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option(
    "--body-file", default=None, help="Read field text from this file, or - for stdin."
)
@click.option("--clear", is_flag=True, default=False, help="Clear nullable fields.")
@json_option
def ticket_set(
    ticket_id: str,
    field: str,
    value: str | None,
    body_file: str | None,
    clear: bool,
    as_json: bool,
) -> None:
    api_field = _TICKET_SET_FIELDS[field]
    new_value = read_value_or_file(value, body_file, clear, as_json, field)
    if field in {"title", "priority"} and new_value is None:
        http.fail_validation(f"{field} cannot be cleared", as_json)
    if field == "priority" and new_value not in _PRIORITIES:
        http.fail_validation("priority must be P0, P1, P2, or P3", as_json)
    if field == "kickoff-note" and new_value is None:
        new_value = ""
    if field == "kickoff-note":
        data = http.send(
            "PUT",
            f"/api/tickets/{ticket_id}/value/kickoff",
            as_json=as_json,
            json_body={"body": new_value},
            request_actor="ordinary",
        )
    else:
        data = http.send(
            "PATCH",
            f"/api/tickets/{ticket_id}",
            as_json=as_json,
            json_body={api_field: new_value},
            request_actor="ordinary",
        )
    http.emit(data, as_json, f"{data['id']} {field} set")


@ticket.command("place")
@click.argument("ticket_id")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--sprint", default=None, help="Sprint id or current.")
@click.option("--backlog", is_flag=True, default=False, help="Clear Sprint placement.")
@click.option("--sprint-item", default=None, help="Optional Sprint Item classification.")
@click.option(
    "--clear-sprint-item", is_flag=True, default=False, help="Remove classification."
)
@json_option
def ticket_place(
    ticket_id: str,
    project: str | None,
    project_id: str | None,
    sprint: str | None,
    backlog: bool,
    sprint_item: str | None,
    clear_sprint_item: bool,
    as_json: bool,
) -> None:
    if backlog and sprint is not None:
        http.fail_validation("--backlog and --sprint are mutually exclusive", as_json)
    if sprint_item is not None and clear_sprint_item:
        http.fail_validation(
            "--sprint-item and --clear-sprint-item are mutually exclusive", as_json
        )
    body: dict[str, Any] = {}
    add_project_selectors(
        body, project=project, project_id=project_id, required=False, as_json=as_json
    )
    if sprint is not None:
        body["sprint_id"] = sprint_value_for_write(sprint, as_json)
    elif backlog:
        body["sprint_id"] = None
    if sprint_item is not None:
        body["sprint_item_id"] = sprint_item
    elif clear_sprint_item:
        body["sprint_item_id"] = None
    if not body:
        http.fail_validation("placement requires at least one selection", as_json)
    data = http.send(
        "PATCH",
        f"/api/tickets/{ticket_id}",
        as_json=as_json,
        json_body=body,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} placement set")


@ticket.command("employee-configuration")
@click.argument("ticket_id")
@click.option("--backend", required=True, help="Registered employee backend.")
@click.option(
    "--model", required=True, help="Model that backend runs this Ticket's worker on."
)
@click.option(
    "--reasoning-effort",
    default=None,
    help="Reasoning effort for that model; leave it out for a model that takes none.",
)
@json_option
def ticket_employee_configuration(
    ticket_id: str,
    backend: str,
    model: str,
    reasoning_effort: str | None,
    as_json: bool,
) -> None:
    """Set what this Ticket's worker launches on: backend, model, reasoning effort.

    All three go together because the door takes them together — a model id belongs to the
    backend that named it, so there is no such thing as changing one of them on its own.
    """
    data = http.send(
        "PUT",
        f"/api/tickets/{ticket_id}/employee-configuration",
        as_json=as_json,
        json_body={
            "employee_backend": backend,
            "employee_launch_model": model,
            "employee_launch_reasoning_effort": reasoning_effort,
        },
        request_actor="ordinary",
    )
    http.emit(
        data,
        as_json,
        f"{data['id']} employee configuration set {backend} {model}",
    )


@ticket.command("ownership")
@click.argument("ticket_id")
@click.option("--stage", required=True, help="Stage id to override.")
@click.option(
    "--mode",
    required=True,
    type=click.Choice(["worker", "user", "paired", "default"]),
    help="Ownership mode; default clears the override.",
)
@json_option
def ticket_ownership(ticket_id: str, stage: str, mode: str, as_json: bool) -> None:
    data = http.send(
        "PUT",
        f"/api/tickets/{ticket_id}/stage-ownership/{stage}",
        as_json=as_json,
        json_body={"ownership_mode": None if mode == "default" else mode},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} {stage} ownership set")


@ticket.command("approve")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--ceiling", default=None, help="Next ceiling stage or none.")
@click.option(
    "--at-cap",
    default=None,
    type=click.Choice([a.value for a in AtCap]),
    help="propose or stop.",
)
@click.option("--edit-file", default=None, help="Edited accepted body, or - for stdin.")
@click.option("--kickoff-title", default=None, help="Edited Kickoff title.")
@click.option(
    "--kickoff-note-file", default=None, help="Edited Kickoff note, or - for stdin."
)
@json_option
def ticket_approve(
    ticket_id: str | None,
    ceiling: str | None,
    at_cap: str | None,
    edit_file: str | None,
    kickoff_title: str | None,
    kickoff_note_file: str | None,
    as_json: bool,
) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    detail = http.send(
        "GET", f"/api/tickets/{tid}", as_json=as_json, request_actor="ordinary"
    )
    stage = detail["stage"]
    manifest = _worker_type(detail["worker_type"], as_json)
    field = _gating_field_for_stage(manifest, stage)
    if field is None:
        http.fail_validation(f"ticket in {stage} has nothing to approve", as_json)
    proposal = detail["fields"][field]["proposal"]
    if proposal is None:
        http.fail_validation(f"no pending {field} proposal", as_json)
    if ceiling is None or at_cap is None:
        http.fail_validation("approval requires --ceiling and --at-cap", as_json)
    if kickoff_title is not None:
        if field != "kickoff":
            http.fail_validation(
                "--kickoff-title only applies while approving kickoff", as_json
            )
        http.send(
            "PATCH",
            f"/api/tickets/{tid}",
            as_json=as_json,
            json_body={"title": kickoff_title},
            request_actor="ordinary",
        )
    field_payload: dict[str, Any] = {"next_ceiling": ceiling, "at_cap": at_cap}
    if edit_file is not None:
        field_payload["edited_body"] = _read_source(edit_file, as_json)
    if kickoff_note_file is not None:
        if field != "kickoff":
            http.fail_validation(
                "--kickoff-note-file only applies while approving kickoff", as_json
            )
        if "edited_body" in field_payload:
            http.fail_validation(
                "approval accepts only one edited body option", as_json
            )
        field_payload["edited_body"] = _read_source(kickoff_note_file, as_json)
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/accept/{field}",
        as_json=as_json,
        json_body=field_payload,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} approved {field}")


@ticket.command("block")
@click.argument("ticket_id")
@click.option("--by", "blocker_id", required=True, help="Blocking ticket id.")
@json_option
def ticket_block(ticket_id: str, blocker_id: str, as_json: bool) -> None:
    data = http.send(
        "POST",
        "/api/links",
        as_json=as_json,
        json_body={"from_id": blocker_id, "to_id": ticket_id, "kind": "blocks"},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{ticket_id} blocked by {blocker_id}")


@ticket.command("unblock")
@click.argument("ticket_id")
@click.option("--by", "blocker_id", required=True, help="Blocking ticket id.")
@json_option
def ticket_unblock(ticket_id: str, blocker_id: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        "/api/links",
        as_json=as_json,
        params={"from_id": blocker_id, "to_id": ticket_id, "kind": "blocks"},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{ticket_id} unblocked from {blocker_id}")


@ticket.command("copy")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def ticket_copy(ticket_id: str | None, as_json: bool) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    text = http.send_text(
        "GET",
        f"/api/tickets/{tid}/copy-text",
        as_json=as_json,
        request_actor="ordinary",
    )
    http.emit({"text": text}, as_json, text)


# --- sprint -------------------------------------------------------------------


@main.group("sprint")
def sprint() -> None:
    """Create, inspect, edit, and populate sprints."""


@sprint.command("create")
@click.option("--name", required=True, help="Sprint name.")
@click.option("--date-start", required=True, help="YYYY-MM-DD.")
@click.option("--date-end", required=True, help="YYYY-MM-DD.")
@click.option("--limiting-factor", default="", help="Kickoff limiting factor.")
@click.option("--primary-bet", default="", help="Kickoff primary bet.")
@click.option("--supports", default="", help="Kickoff supports.")
@click.option("--premortem", default="", help="Kickoff premortem.")
@json_option
def sprint_create(
    name: str,
    date_start: str,
    date_end: str,
    limiting_factor: str,
    primary_bet: str,
    supports: str,
    premortem: str,
    as_json: bool,
) -> None:
    body = {
        "name": name,
        "date_start": date_start,
        "date_end": date_end,
        "limiting_factor": limiting_factor,
        "primary_bet": primary_bet,
        "supports": supports,
        "premortem": premortem,
    }
    data = http.send(
        "POST",
        "/api/sprints",
        as_json=as_json,
        json_body=body,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} {data['name']}")


@sprint.command("list")
@bounded_options
@json_option
def sprint_list(limit: int, offset: int, as_json: bool) -> None:
    data = http.send(
        "GET",
        "/api/sprint-summaries",
        as_json=as_json,
        params={"limit": limit, "offset": offset},
        request_actor="ordinary",
    )
    http.emit(
        data,
        as_json,
        _format_bounded(
            _lines(
                data["sprints"],
                lambda s: f"{s['id']} {s['date_start']}..{s['date_end']} {s['name']}",
            ),
            data["page"],
        ),
    )


@sprint.command("show")
@click.argument("sprint_id", required=False)
@click.argument("part_names", required=False)
@json_option
def sprint_show(
    sprint_id: str | None, part_names: str | None, as_json: bool
) -> None:
    """Show a Sprint manifest or selected comma-separated PART_NAMES."""
    if sprint_id is None or sprint_id == "current":
        current_response = http.send(
            "GET", "/api/sprint/current", as_json=as_json, request_actor="ordinary"
        )
        data = current_response["sprint"]
        if data is None:
            http.fail_validation("no current sprint", as_json)
    else:
        data = http.send(
            "GET",
            f"/api/sprints/{sprint_id}",
            as_json=as_json,
            request_actor="ordinary",
        )
    header, parts = _sprint_record(data)
    _emit_record(header, parts, part_names, as_json)


@sprint.command("set")
@click.argument("sprint_id")
@click.argument("field", type=click.Choice(sorted(_SPRINT_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option(
    "--body-file", default=None, help="Read field text from this file, or - for stdin."
)
@click.option(
    "--clear", is_flag=True, default=False, help="Set the field to empty text."
)
@json_option
def sprint_set(
    sprint_id: str,
    field: str,
    value: str | None,
    body_file: str | None,
    clear: bool,
    as_json: bool,
) -> None:
    resolved_sprint_id = sprint_value_for_write(sprint_id, as_json)
    if resolved_sprint_id is None:
        http.fail_validation("sprint set requires a sprint id or current", as_json)
    api_field = _SPRINT_FIELDS[field]
    new_value = read_value_or_file(value, body_file, clear, as_json, field)
    if field in {"name", "date-start", "date-end"} and new_value is None:
        http.fail_validation(f"{field} cannot be cleared", as_json)
    data = http.send(
        "PATCH",
        f"/api/sprints/{resolved_sprint_id}",
        as_json=as_json,
        json_body={api_field: "" if new_value is None else new_value},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


@sprint.group("item")
def sprint_item() -> None:
    """Create, inspect, edit, and populate sprint items."""


@sprint_item.command("create")
@click.option("--title", required=True, help="Sprint item title.")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--body-file", default=None, help="Optional body file, or - for stdin.")
@click.option(
    "--priority", type=click.Choice(_PRIORITIES), default=None, help="Priority label."
)
@click.option("--deadline", default=None, help="Due date in YYYY-MM-DD form.")
@click.option("--sprint", default=None, help="Sprint id, current, or none.")
@json_option
def sprint_item_create(
    title: str,
    project: str | None,
    project_id: str | None,
    body_file: str | None,
    priority: str | None,
    deadline: str | None,
    sprint: str | None,
    as_json: bool,
) -> None:
    body: dict[str, Any] = {
        "title": title,
        "body": read_optional_body(body_file, as_json) or "",
    }
    add_project_selectors(
        body, project=project, project_id=project_id, required=True, as_json=as_json
    )
    if priority is not None:
        body["priority"] = priority
    if deadline is not None:
        body["deadline"] = deadline
    if sprint is not None:
        body["sprint_id"] = sprint_value_for_write(sprint, as_json)
    data = http.send(
        "POST", "/api/items", as_json=as_json, json_body=body, request_actor="ordinary"
    )
    http.emit(data, as_json, f"{data['id']} {data['status']}")


@sprint_item.command("list")
@click.option("--status", default=None, help="Only show items with this status.")
@click.option("--project", default=None, help="Only show project name.")
@click.option("--project-id", default=None, help="Only show project id.")
@click.option("--sprint", default=None, help="Sprint id, current, or none.")
@bounded_options
@json_option
def sprint_item_list(
    status: str | None,
    project: str | None,
    project_id: str | None,
    sprint: str | None,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    params = _drop_none(
        {
            "status": status,
            "project": project,
            "project_id": project_id,
            "sprint_id": sprint_value_for_filter(sprint, as_json),
            "limit": limit,
            "offset": offset,
        }
    )
    data = http.send(
        "GET",
        "/api/sprint-item-summaries",
        as_json=as_json,
        params=params,
        request_actor="ordinary",
    )
    http.emit(
        data,
        as_json,
        _format_bounded(
            _lines(
                data["items"],
                lambda i: f"{i['id']} {i['status']} {i['priority']} {i['title']}",
            ),
            data["page"],
        ),
    )


@sprint_item.command("show")
@click.argument("item_id")
@click.argument("part_names", required=False)
@json_option
def sprint_item_show(item_id: str, part_names: str | None, as_json: bool) -> None:
    """Show an Item manifest or selected comma-separated PART_NAMES."""
    data = http.send(
        "GET", f"/api/items/{item_id}", as_json=as_json, request_actor="ordinary"
    )
    header, parts = _sprint_item_record(data)
    _emit_record(header, parts, part_names, as_json)


@sprint_item.command("delete")
@click.argument("item_id")
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Permanently delete the sprint item and its history.",
)
@json_option
def sprint_item_delete(item_id: str, yes: bool, as_json: bool) -> None:
    if not yes:
        http.fail_validation("permanent deletion requires --yes", as_json)
    data = http.send(
        "DELETE", f"/api/items/{item_id}", as_json=as_json, request_actor="ordinary"
    )
    http.emit(data, as_json, f"{item_id} permanently deleted")


@sprint_item.command("set")
@click.argument("item_id")
@click.argument("field", type=click.Choice(sorted(_ITEM_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option(
    "--body-file", default=None, help="Read field text from this file, or - for stdin."
)
@click.option("--clear", is_flag=True, default=False, help="Clear nullable fields.")
@json_option
def sprint_item_set(
    item_id: str,
    field: str,
    value: str | None,
    body_file: str | None,
    clear: bool,
    as_json: bool,
) -> None:
    api_field = _ITEM_FIELDS[field]
    new_value = read_value_or_file(value, body_file, clear, as_json, field)
    if field in {"title", "priority", "project", "project-id"} and new_value is None:
        http.fail_validation(f"{field} cannot be cleared", as_json)
    if field == "priority" and new_value not in _PRIORITIES:
        http.fail_validation("priority must be P0, P1, P2, or P3", as_json)
    if field == "sprint" and new_value is not None:
        new_value = sprint_value_for_write(new_value, as_json)
    data = http.send(
        "PATCH",
        f"/api/items/{item_id}",
        as_json=as_json,
        json_body={api_field: new_value},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


@sprint_item.command("move-ticket")
@click.argument("item_id")
@click.argument("ticket_id")
@json_option
def sprint_item_move_ticket(item_id: str, ticket_id: str, as_json: bool) -> None:
    data = http.send(
        "POST",
        f"/api/items/{item_id}/tickets",
        as_json=as_json,
        json_body={"ticket_id": ticket_id},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{ticket_id} added to {item_id}")


@sprint_item.command("move-ticket-to-backlog")
@click.argument("item_id")
@click.argument("ticket_id")
@json_option
def sprint_item_move_ticket_to_backlog(
    item_id: str, ticket_id: str, as_json: bool
) -> None:
    data = http.send(
        "DELETE",
        f"/api/items/{item_id}/tickets/{ticket_id}",
        as_json=as_json,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{ticket_id} removed from {item_id}")


@sprint_item.command("block")
@click.argument("item_id")
@click.option("--by", "blocker_id", required=True, help="Blocking ticket id.")
@json_option
def sprint_item_block(item_id: str, blocker_id: str, as_json: bool) -> None:
    data = http.send(
        "POST",
        "/api/links",
        as_json=as_json,
        json_body={"from_id": blocker_id, "to_id": item_id, "kind": "blocks"},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{item_id} blocked by {blocker_id}")


@sprint_item.command("unblock")
@click.argument("item_id")
@click.option("--by", "blocker_id", required=True, help="Blocking ticket id.")
@json_option
def sprint_item_unblock(item_id: str, blocker_id: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        "/api/links",
        as_json=as_json,
        params={"from_id": blocker_id, "to_id": item_id, "kind": "blocks"},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{item_id} unblocked from {blocker_id}")


@sprint_item.group("supervisor")
def sprint_item_supervisor() -> None:
    """Inspect and talk to a Sprint Item supervisor."""


@sprint_item_supervisor.command("show")
@click.argument("item_id")
@json_option
def sprint_item_supervisor_show(item_id: str, as_json: bool) -> None:
    data = http.send(
        "GET", f"/api/items/{item_id}/supervisor", as_json=as_json,
        request_actor="ordinary",
    )
    launch = data["launch_configuration"]
    http.emit(
        data,
        as_json,
        f"{data['agent_key']} {launch['employee_backend']} / "
        f"{launch['employee_launch_model']}",
    )


@sprint_item_supervisor.command("context")
@click.argument("item_id")
@json_option
def sprint_item_supervisor_context(item_id: str, as_json: bool) -> None:
    data = http.send(
        "GET", f"/api/items/{item_id}/supervisor/context", as_json=as_json,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{item_id} {len(data['tickets'])} current Tickets")


@sprint_item_supervisor.command("send")
@click.argument("item_id")
@click.option("--message", default=None, help="Message text.")
@click.option("--body-file", default=None, help="Read message text from this file, or -.")
@json_option
def sprint_item_supervisor_send(
    item_id: str, message: str | None, body_file: str | None, as_json: bool
) -> None:
    if (message is None) == (body_file is None):
        http.fail_validation("send requires exactly one of --message or --body-file", as_json)
    if message is not None:
        text = message
    else:
        assert body_file is not None
        text = _read_source(body_file, as_json)
    data = http.send(
        "POST",
        f"/api/items/{item_id}/supervisor/conversation/send",
        as_json=as_json,
        json_body={
            "content": [{"piece": "text", "text": text}],
            "sender_label": "You",
        },
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"message {data['fate']}")


@sprint_item_supervisor.command("reset")
@click.argument("item_id")
@json_option
def sprint_item_supervisor_reset(item_id: str, as_json: bool) -> None:
    data = http.send(
        "POST", f"/api/items/{item_id}/supervisor/conversation/reset",
        as_json=as_json, request_actor="ordinary",
    )
    http.emit(data, as_json, f"{item_id} supervisor conversation reset")


# --- chief --------------------------------------------------------------------

_EXTERNAL_WORK_RECONCILE_FIXED_KEYS = frozenset({"stage", "kickoff_note", "recap"})
_EXTERNAL_WORK_CREATE_FIXED_KEYS = _EXTERNAL_WORK_RECONCILE_FIXED_KEYS | frozenset(
    {
        "title",
        "worker_type",
        "employee_backend",
        "employee_launch_model",
        "priority",
        "deadline",
        "project",
        "project_id",
        "sprint_item_id",
        "blocked_by_ticket_ids",
    }
)


def _external_work_body(
    *,
    stage: str,
    kickoff_note_file: str,
    recap_file: str | None,
    success_file: str | None,
    approach_file: str | None,
    plan_file: str | None,
    implementation_file: str | None,
    closeout_file: str | None,
    field_files: tuple[str, ...],
    reserved_fixed_keys: frozenset[str],
    as_json: bool,
) -> dict[str, Any]:
    field_sources: dict[str, str] = {}
    for field, source in (
        ("success", success_file),
        ("approach", approach_file),
        ("plan", plan_file),
        ("implementation", implementation_file),
        ("closeout", closeout_file),
    ):
        if source is not None:
            field_sources[field] = source
    for occurrence in field_files:
        if "=" not in occurrence:
            http.fail_validation(
                f"field file must be FIELD=PATH: {occurrence}", as_json
            )
        field, source = occurrence.split("=", 1)
        if not field or not source:
            http.fail_validation(
                f"field file must be FIELD=PATH: {occurrence}", as_json
            )
        if field in reserved_fixed_keys:
            http.fail_validation(
                f"field file conflicts with fixed request key: {field}", as_json
            )
        if field in field_sources:
            http.fail_validation(
                f"field file provided more than once: {field}", as_json
            )
        field_sources[field] = source

    body: dict[str, Any] = {
        "stage": stage,
        "kickoff_note": read_required_option_body(
            kickoff_note_file, as_json, "kickoff-note"
        ),
    }
    if recap_file is not None:
        body["recap"] = _read_source(recap_file, as_json)
    for field, source in field_sources.items():
        body[field] = _read_source(source, as_json)
    return body


@main.group("chief")
def chief() -> None:
    """Import work already completed outside Panels."""


@chief.command("reconcile-ticket-from-external-work")
@click.argument("ticket_id")
@click.option(
    "--stage", required=True, help="Target worker stage (validated per type)."
)
@click.option(
    "--kickoff-note-file",
    required=True,
    help="Complete resulting ticket note file, or -.",
)
@click.option("--recap-file", default=None, help="Read the recap from this file, or -.")
@click.option(
    "--success-file", default=None, help="Read settled success from this file, or -."
)
@click.option(
    "--approach-file", default=None, help="Read settled approach from this file, or -."
)
@click.option(
    "--plan-file", default=None, help="Read settled plan from this file, or -."
)
@click.option(
    "--implementation-file",
    default=None,
    help="Read settled implementation from this file, or -.",
)
@click.option(
    "--closeout-file", default=None, help="Read settled closeout from this file, or -."
)
@click.option(
    "--field-file",
    "field_files",
    multiple=True,
    help="Read a definition-specific settled field from FIELD=PATH (repeatable).",
)
@json_option
def chief_reconcile_ticket_from_external_work(
    ticket_id: str,
    stage: str,
    kickoff_note_file: str,
    recap_file: str | None,
    success_file: str | None,
    approach_file: str | None,
    plan_file: str | None,
    implementation_file: str | None,
    closeout_file: str | None,
    field_files: tuple[str, ...],
    as_json: bool,
) -> None:
    body = _external_work_body(
        stage=stage,
        kickoff_note_file=kickoff_note_file,
        recap_file=recap_file,
        success_file=success_file,
        approach_file=approach_file,
        plan_file=plan_file,
        implementation_file=implementation_file,
        closeout_file=closeout_file,
        field_files=field_files,
        reserved_fixed_keys=_EXTERNAL_WORK_RECONCILE_FIXED_KEYS,
        as_json=as_json,
    )
    data = http.send(
        "POST",
        f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
        as_json=as_json,
        json_body=body,
        request_actor="chief",
    )
    http.emit(
        data,
        as_json,
        f"{data['id']} external work reconciled {data['stage']}",
    )


@chief.command("create-ticket-from-external-work")
@click.option("--title", required=True, help="Ticket title.")
@click.option(
    "--worker-type",
    "worker_type",
    required=True,
    help="Registered Worker type id. List choices with `panels worker-type list`.",
)
@click.option(
    "--employee-backend", default=None, help="Registered employee backend override."
)
@click.option(
    "--employee-launch-model",
    default=None,
    help="Model for an overriding backend; required when it is not the Worker type's own.",
)
@click.option(
    "--stage", required=True, help="Target worker stage (validated per type)."
)
@click.option(
    "--kickoff-note-file",
    required=True,
    help="Complete resulting ticket note file, or -.",
)
@click.option("--recap-file", default=None, help="Read the recap from this file, or -.")
@click.option(
    "--success-file", default=None, help="Read settled success from this file, or -."
)
@click.option(
    "--approach-file", default=None, help="Read settled approach from this file, or -."
)
@click.option(
    "--plan-file", default=None, help="Read settled plan from this file, or -."
)
@click.option(
    "--implementation-file",
    default=None,
    help="Read settled implementation from this file, or -.",
)
@click.option(
    "--closeout-file", default=None, help="Read settled closeout from this file, or -."
)
@click.option(
    "--field-file",
    "field_files",
    multiple=True,
    help="Read a definition-specific settled field from FIELD=PATH (repeatable).",
)
@click.option(
    "--priority", type=click.Choice(_PRIORITIES), default=None, help="Priority label."
)
@click.option("--deadline", default=None, help="Due date in YYYY-MM-DD form.")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--sprint", "sprint_id", default=None, help="Sprint id.")
@click.option(
    "--sprint-item", "sprint_item", default=None, help="Parent sprint item id."
)
@click.option(
    "--backlog", is_flag=True, default=False, help="Leave the Ticket unparented."
)
@click.option(
    "--blocked-by",
    "blocked_by_ticket_ids",
    multiple=True,
    help="Existing blocking Ticket id (repeatable).",
)
@json_option
def chief_create_ticket_from_external_work(
    title: str,
    worker_type: str,
    employee_backend: str | None,
    employee_launch_model: str | None,
    stage: str,
    kickoff_note_file: str,
    recap_file: str | None,
    success_file: str | None,
    approach_file: str | None,
    plan_file: str | None,
    implementation_file: str | None,
    closeout_file: str | None,
    field_files: tuple[str, ...],
    priority: str | None,
    deadline: str | None,
    project: str | None,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item: str | None,
    backlog: bool,
    blocked_by_ticket_ids: tuple[str, ...],
    as_json: bool,
) -> None:
    body = _external_work_body(
        stage=stage,
        kickoff_note_file=kickoff_note_file,
        recap_file=recap_file,
        success_file=success_file,
        approach_file=approach_file,
        plan_file=plan_file,
        implementation_file=implementation_file,
        closeout_file=closeout_file,
        field_files=field_files,
        reserved_fixed_keys=_EXTERNAL_WORK_CREATE_FIXED_KEYS,
        as_json=as_json,
    )
    body["title"] = title
    body["worker_type"] = worker_type
    if employee_backend is not None:
        body["employee_backend"] = employee_backend
    if employee_launch_model is not None:
        body["employee_launch_model"] = employee_launch_model
    if priority is not None:
        body["priority"] = priority
    if deadline is not None:
        body["deadline"] = deadline
    add_project_selectors(
        body, project=project, project_id=project_id, required=False, as_json=as_json
    )
    if sprint_id is not None:
        body["sprint_id"] = sprint_value_for_write(sprint_id, as_json)
    if backlog and (sprint_item is not None or sprint_id is not None):
        http.fail_validation(
            "--backlog cannot be combined with --sprint or --sprint-item", as_json
        )
    if sprint_item is not None:
        body["sprint_item_id"] = sprint_item
    elif backlog:
        body["sprint_item_id"] = None
    if blocked_by_ticket_ids:
        body["blocked_by_ticket_ids"] = list(blocked_by_ticket_ids)
    data = http.send(
        "POST",
        "/api/chief/tickets/from-external-work",
        as_json=as_json,
        json_body=body,
        request_actor="chief",
    )
    http.emit(data, as_json, f"{data['id']} external work created {data['stage']}")


# --- worker -------------------------------------------------------------------


@main.group("worker")
def worker() -> None:
    """Worker-only ticket and item writes."""


@worker.command("my-ticket")
@click.argument("part_names", required=False)
@json_option
def worker_my_ticket(part_names: str | None, as_json: bool) -> None:
    """Show the worker's Ticket manifest or selected comma-separated PART_NAMES."""
    ticket_id = os.environ.get(_TICKET_ID_ENV, "").strip()
    if not ticket_id:
        http.fail_validation(
            "no ticket worker identity in env (PLAN_TICKET_ID is missing); "
            "not running as a ticket worker",
            as_json,
        )
    data = http.send(
        "GET",
        f"/api/tickets/{ticket_id}/worker-self",
        as_json=as_json,
    )
    header, parts = _ticket_record(data)
    _emit_record(header, parts, part_names, as_json)


@worker.command("propose")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option(
    "--body-file", required=True, help="Read proposal text from this file, or -."
)
@click.option("--recap", default=None, help="Short recap for the proposal.")
@click.option("--recap-file", default=None, help="Read recap from this file, or -.")
@json_option
def worker_propose(
    ticket_id: str | None,
    body_file: str,
    recap: str | None,
    recap_file: str | None,
    as_json: bool,
) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    body = read_required_option_body(body_file, as_json, "body")
    recap_text = read_recap(recap, recap_file, as_json)
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/propose",
        as_json=as_json,
        json_body={"body": body, "recap": recap_text},
    )
    http.emit(data, as_json, f"proposed on {data['id']}")


@worker.command("request-user-help")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def worker_request_user_help(ticket_id: str | None, as_json: bool) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("POST", f"/api/tickets/{tid}/request-user-help", as_json=as_json)
    http.emit(data, as_json, f"user help requested on {data['id']}")


@worker.command("trouble")
@click.option(
    "--body-file", required=True, help="Read the one-line trouble note from this file, or -."
)
@json_option
def worker_trouble(body_file: str, as_json: bool) -> None:
    """Record trouble on the current worker's Ticket."""
    tid = resolve_ticket_id(None, as_json)
    body = read_required_option_body(body_file, as_json, "body")
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/trouble-notes",
        as_json=as_json,
        json_body={"body": body},
    )
    note = data["trouble_note"]
    http.emit(data, as_json, f"trouble recorded on {tid} as note {note['sequence']}")


@worker.command("recap")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Read recap text from this file, or -.")
@json_option
def worker_recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "PUT", f"/api/tickets/{tid}/recap", as_json=as_json, json_body={"body": body}
    )
    http.emit(data, as_json, f"recap written on {data['id']}")


@worker.command("note")
@click.argument("args", nargs=-1)
@click.option(
    "--body-file",
    default=None,
    help="Read field user guidance text from this file, or -.",
)
@click.option(
    "--append",
    "append_note",
    is_flag=True,
    help="Append the body to the existing field user note.",
)
@click.option(
    "--replace",
    "replace_note",
    is_flag=True,
    help="Replace the complete field user note. This is the default.",
)
@json_option
def worker_note(
    args: tuple[str, ...],
    body_file: str | None,
    append_note: bool,
    replace_note: bool,
    as_json: bool,
) -> None:
    if append_note and replace_note:
        http.fail_validation("use --append or --replace, not both", as_json)
    if len(args) == 1:
        ticket_id: str | None = None
        field = args[0]
    elif len(args) == 2:
        ticket_id = args[0]
        field = args[1]
    else:
        http.fail_validation("usage: worker note [ticket-id] <field>", as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    detail = http.send("GET", f"/api/tickets/{tid}", as_json=as_json)
    manifests = http.send("GET", "/api/worker-types", as_json=as_json)
    worker_type = detail.get("worker_type")
    manifest = next(
        (
            candidate
            for candidate in manifests.get("worker_types", [])
            if candidate.get("worker_type") == worker_type
        ),
        None,
    )
    fields = (
        [
            candidate.get("id")
            for candidate in manifest.get("fields", [])
            if candidate.get("id") != "kickoff"
        ]
        if isinstance(manifest, dict)
        else []
    )
    if field not in fields:
        http.fail_validation(
            f"field must be {', '.join(str(candidate) for candidate in fields[:-1])}"
            f"{', or ' if len(fields) > 1 else ''}{fields[-1] if fields else ''}",
            as_json,
        )
    body = read_body(ticket_id, body_file, as_json)
    method = "POST" if append_note else "PUT"
    path = (
        f"/api/tickets/{tid}/notes/{field}/append"
        if append_note
        else f"/api/tickets/{tid}/notes/{field}"
    )
    data = http.send(
        method,
        path,
        as_json=as_json,
        json_body={"user_note": body},
    )
    http.emit(data, as_json, f"user note {field} written on {data['id']}")


if __name__ == "__main__":
    main()
