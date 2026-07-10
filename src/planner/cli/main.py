"""The `panels` CLI.

The command tree mirrors the product model:

* day: plan and inspect a planning day
* ticket: create, inspect, organize, and approve tickets
* sprint: create, inspect, edit, and populate sprints and sprint items
* worker: worker-only writes such as proposals, recaps, and notes
* chief: import work completed outside Panels
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
from click.core import ParameterSource

from planner.cli import http
from planner.tickets.contracts import GATING_FIELD, STATE_ORDER, AtCap, TicketState

_PRIORITIES = ["P0", "P1", "P2", "P3"]
_FIELDS = ["success", "approach", "plan", "result"]
_TICKET_ID_ENV = "PLAN_TICKET_ID"

_DAY_FIELDS = {
    "focus": "focus",
    "brief-take": "brief_take",
    "watchout": "watchout",
    "if-today-lands": "if_today_lands",
    "notes": "notes",
}

_TICKET_SET_FIELDS = {
    "title": "title",
    "user-note": "user_note",
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
    "summary": "summary",
}


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


def read_body(positional_ticket_id: str | None, body_file: str | None, as_json: bool) -> str:
    if body_file is not None:
        text = _read_source(body_file, as_json)
    elif _positional_is_stdin_marker(positional_ticket_id):
        text = sys.stdin.read()
    else:
        http.fail_validation("body required: pass '-' for stdin or --body-file PATH", as_json)
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
    sources = sum(1 for present in (value is not None, body_file is not None, clear) if present)
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
        http.fail_validation("recap accepts only one of --recap or --recap-file", as_json)
    if recap_file is not None:
        text = _read_source(recap_file, as_json)
    elif recap is not None:
        text = recap
    else:
        http.fail_validation("recap required: pass --recap TEXT or --recap-file PATH", as_json)
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
    data = http.send("GET", "/api/sprint/current", as_json=as_json, request_actor="ordinary")
    sprint = data.get("sprint") if isinstance(data, dict) else None
    if sprint is None:
        http.fail_validation("no current sprint", as_json)
    return str(sprint["id"])


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
        lambda t: f"{t['id']} {t['state']} {t['priority']} {t['title']}",
    )


def _format_day(data: dict[str, Any]) -> str:
    lines = [
        f"day {data['id']}",
        f"focus: {data['focus'] or ''}",
        f"brief-take: {data['brief_take'] or ''}",
        f"watchout: {data['watchout'] or ''}",
        f"if-today-lands: {data['if_today_lands'] or ''}",
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
        http.fail_validation("project required: pass --project or --project-id", as_json)
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
        help="Print the full JSON response. Errors are printed as JSON on stderr.",
    )(func)


@click.group()
def main() -> None:
    """Operate the local planner server."""


@main.command("serve")
def serve() -> None:
    """Start the web server and background planner runtime."""
    import os

    import uvicorn

    from planner.core.adapters.registry import build_adapters
    from planner.core.clock import build_clock
    from planner.core.config import HOST, load_config
    from planner.core.db import connect, create_schema
    from planner.core.server import create_app

    repo_root = Path(__file__).resolve().parents[3]
    os.chdir(repo_root)
    config = load_config(str(repo_root / "config.yaml"))
    os.makedirs(os.path.dirname(config.db_path) or ".", exist_ok=True)
    os.makedirs(config.logs_dir, exist_ok=True)
    with connect(config.db_path, config.db_busy_timeout_ms) as bootstrap:
        create_schema(bootstrap)

    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> sqlite3.Connection:
        return connect(config.db_path, config.db_busy_timeout_ms)

    app = create_app(config, clock, adapters, conn_factory)
    uvicorn.run(app, host=HOST, port=config.port)


# --- project ------------------------------------------------------------------


@main.group("project")
def project_group() -> None:
    """List and create projects."""


@project_group.command("list")
@json_option
def project_list(as_json: bool) -> None:
    data = http.send("GET", "/api/projects", as_json=as_json, request_actor="ordinary")
    http.emit(data, as_json, _lines(data["projects"], lambda p: f"{p['id']} {p['name']}"))


@project_group.command("create")
@click.option("--name", required=True, help="Project display name.")
@click.option("--summary", default=None, help="Optional project summary text.")
@json_option
def project_create(name: str, summary: str | None, as_json: bool) -> None:
    body: dict[str, Any] = {"name": name}
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
@click.option("--body-file", default=None, help="Read field text from this file, or - for stdin.")
@click.option("--clear", is_flag=True, default=False, help="Set the field to empty text.")
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
    if field == "name" and new_value is None:
        http.fail_validation("name cannot be cleared", as_json)
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
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@json_option
def day_show(date_: str, as_json: bool) -> None:
    data = http.send("GET", f"/api/day/{date_}", as_json=as_json, request_actor="ordinary")
    http.emit(data, as_json, _format_day(data))


@day.command("list-tickets")
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@json_option
def day_list_tickets(date_: str, as_json: bool) -> None:
    data = http.send("GET", f"/api/day/{date_}", as_json=as_json, request_actor="ordinary")
    payload = {"id": data["id"], "tickets": data["tickets"]}
    http.emit(payload, as_json, _format_tickets(data["tickets"]))


@day.command("set")
@click.argument("field", type=click.Choice(sorted(_DAY_FIELDS)))
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@click.option("--value", default=None, help="Set the field to this text.")
@click.option("--body-file", default=None, help="Read field text from this file, or - for stdin.")
@click.option("--clear", is_flag=True, default=False, help="Set the field to empty text.")
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
@click.option("--priority", type=click.Choice(_PRIORITIES), default=None, help="Priority label.")
@click.option("--deadline", default=None, help="Due date in YYYY-MM-DD form.")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--sprint", default=None, help="Sprint id, current, or none.")
@click.option("--sprint-item", "sprint_item", default=None, help="Parent sprint item id.")
@click.option("--user-note", default=None, help="Preserved intake context / user guidance.")
@click.option("--user-note-file", default=None, help="Read intake user note from this file, or -.")
@json_option
def ticket_create(
    title: str,
    priority: str | None,
    deadline: str | None,
    project: str | None,
    project_id: str | None,
    sprint: str | None,
    sprint_item: str | None,
    user_note: str | None,
    user_note_file: str | None,
    as_json: bool,
) -> None:
    body: dict[str, Any] = {"title": title}
    if user_note is not None and user_note_file is not None:
        http.fail_validation(
            "user note accepts only one of --user-note or --user-note-file", as_json
        )
    if user_note_file is not None:
        body["user_note"] = _read_source(user_note_file, as_json)
    elif user_note is not None:
        body["user_note"] = user_note
    if priority is not None:
        body["priority"] = priority
    if deadline is not None:
        body["deadline"] = deadline
    add_project_selectors(
        body, project=project, project_id=project_id, required=False, as_json=as_json
    )
    if sprint is not None:
        body["sprint_id"] = sprint_value_for_write(sprint, as_json)
    if sprint_item is not None:
        body["sprint_item_id"] = sprint_item
    data = http.send(
        "POST", "/api/tickets", as_json=as_json, json_body=body, request_actor="ordinary"
    )
    http.emit(data, as_json, f"{data['id']} {data['state']}")


@ticket.command("show")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def ticket_show(ticket_id: str | None, as_json: bool) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("GET", f"/api/tickets/{tid}", as_json=as_json, request_actor="ordinary")
    http.emit(data, as_json, f"{data['id']} {data['state']} {data['priority']} {data['title']}")


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
@click.option("--state", default=None, help="Only show tickets in this state.")
@click.option("--project", default=None, help="Only show project name.")
@click.option("--project-id", default=None, help="Only show project id.")
@click.option("--sprint", default=None, help="Sprint id, current, or none.")
@click.option("--sprint-item", "sprint_item", default=None, help="Only show tickets in this item.")
@click.option("--day", default=None, help="today or YYYY-MM-DD.")
@json_option
def ticket_list(
    state: str | None,
    project: str | None,
    project_id: str | None,
    sprint: str | None,
    sprint_item: str | None,
    day: str | None,
    as_json: bool,
) -> None:
    params = _drop_none(
        {
            "state": state,
            "project": project,
            "project_id": project_id,
            "sprint_id": sprint_value_for_filter(sprint, as_json),
            "sprint_item_id": sprint_item,
            "day": day,
        }
    )
    data = http.send(
        "GET", "/api/tickets", as_json=as_json, params=params, request_actor="ordinary"
    )
    http.emit(data, as_json, _format_tickets(data["tickets"]))


@ticket.command("set")
@click.argument("ticket_id")
@click.argument("field", type=click.Choice(sorted(_TICKET_SET_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option("--body-file", default=None, help="Read field text from this file, or - for stdin.")
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
    if field == "user-note" and new_value is None:
        new_value = ""
    data = http.send(
        "PATCH",
        f"/api/tickets/{ticket_id}",
        as_json=as_json,
        json_body={api_field: new_value},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


@ticket.command("approve")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--ceiling", default=None, help="Next ceiling state or none.")
@click.option(
    "--at-cap",
    default=None,
    type=click.Choice([a.value for a in AtCap]),
    help="propose or stop.",
)
@click.option("--edit-file", default=None, help="Edited accepted body, or - for stdin.")
@json_option
def ticket_approve(
    ticket_id: str | None,
    ceiling: str | None,
    at_cap: str | None,
    edit_file: str | None,
    as_json: bool,
) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    detail = http.send("GET", f"/api/tickets/{tid}", as_json=as_json, request_actor="ordinary")
    state = TicketState(detail["state"])
    if state is TicketState.needs_review:
        if ceiling is not None or at_cap is not None or edit_file is not None:
            http.fail_validation("review approval does not accept scope or edited body", as_json)
        data = http.send(
            "POST", f"/api/tickets/{tid}/approve", as_json=as_json, request_actor="ordinary"
        )
        http.emit(data, as_json, f"{data['id']} approved")
    field = GATING_FIELD.get(state)
    if field is None:
        http.fail_validation(f"ticket in {state.value} has nothing to approve", as_json)
    proposal = detail["fields"][field.value]["proposal"]
    if proposal is None:
        http.fail_validation(f"no pending {field.value} proposal", as_json)
    if ceiling is None or at_cap is None:
        http.fail_validation("approval requires --ceiling and --at-cap", as_json)
    payload: dict[str, Any] = {"next_ceiling": ceiling, "at_cap": at_cap}
    if edit_file is not None:
        payload["edited_body"] = _read_source(edit_file, as_json)
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/accept/{field.value}",
        as_json=as_json,
        json_body=payload,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{data['id']} approved {field.value}")


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
        "GET", f"/api/tickets/{tid}/copy-text", as_json=as_json, request_actor="ordinary"
    )
    http.emit({"text": text}, as_json, text)


@ticket.command("events")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def ticket_events(ticket_id: str | None, as_json: bool) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "GET", f"/api/tickets/{tid}/events", as_json=as_json, request_actor="ordinary"
    )
    http.emit(
        data,
        as_json,
        _lines(data["events"], lambda e: f"{e['id']} {e['kind']} {e['created_at']}"),
    )


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
        "POST", "/api/sprints", as_json=as_json, json_body=body, request_actor="ordinary"
    )
    http.emit(data, as_json, f"{data['id']} {data['name']}")


@sprint.command("list")
@json_option
def sprint_list(as_json: bool) -> None:
    data = http.send("GET", "/api/sprints", as_json=as_json, request_actor="ordinary")
    http.emit(
        data,
        as_json,
        _lines(
            data["sprints"],
            lambda s: f"{s['id']} {s['date_start']}..{s['date_end']} {s['name']}",
        ),
    )


@sprint.command("show")
@click.argument("sprint_id", required=False)
@json_option
def sprint_show(sprint_id: str | None, as_json: bool) -> None:
    if sprint_id is None or sprint_id == "current":
        data = http.send("GET", "/api/sprint/current", as_json=as_json, request_actor="ordinary")
        current = data["sprint"]
        human = (
            "no current sprint"
            if current is None
            else f"{current['id']} {current['date_start']}..{current['date_end']} {current['name']}"
        )
        http.emit(data, as_json, human)
    data = http.send(
        "GET", f"/api/sprints/{sprint_id}", as_json=as_json, request_actor="ordinary"
    )
    human = f"{data['id']} {data['date_start']}..{data['date_end']} {data['name']}"
    http.emit(data, as_json, human)


@sprint.command("set")
@click.argument("sprint_id")
@click.argument("field", type=click.Choice(sorted(_SPRINT_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option("--body-file", default=None, help="Read field text from this file, or - for stdin.")
@click.option("--clear", is_flag=True, default=False, help="Set the field to empty text.")
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


@sprint.command("add-ticket")
@click.argument("ticket_id")
@click.option("--sprint", default="current", help="Sprint id or current.")
@json_option
def sprint_add_ticket(ticket_id: str, sprint: str, as_json: bool) -> None:
    target = sprint_value_for_write(sprint, as_json)
    if target is None:
        http.fail_validation("sprint add-ticket requires a sprint id or current", as_json)
    data = http.send(
        "PATCH",
        f"/api/tickets/{ticket_id}",
        as_json=as_json,
        json_body={"sprint_id": target},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{ticket_id} added to sprint {target}")


@sprint.command("remove-ticket")
@click.argument("ticket_id")
@click.option("--sprint", default="current", help="Sprint id or current.")
@json_option
def sprint_remove_ticket(ticket_id: str, sprint: str, as_json: bool) -> None:
    target = sprint_value_for_write(sprint, as_json)
    if target is None:
        http.fail_validation("sprint remove-ticket requires a sprint id or current", as_json)
    detail = http.send(
        "GET", f"/api/tickets/{ticket_id}", as_json=as_json, request_actor="ordinary"
    )
    if detail["sprint_item_id"] is not None:
        http.fail_validation("ticket is parented under a sprint item", as_json)
    if detail["sprint_id"] != target:
        http.fail_validation("ticket is not assigned to that sprint", as_json)
    data = http.send(
        "PATCH",
        f"/api/tickets/{ticket_id}",
        as_json=as_json,
        json_body={"sprint_id": None},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{ticket_id} removed from sprint")


@sprint.group("item")
def sprint_item() -> None:
    """Create, inspect, edit, and populate sprint items."""


@sprint_item.command("create")
@click.option("--title", required=True, help="Sprint item title.")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--body-file", default=None, help="Optional body file, or - for stdin.")
@click.option("--priority", type=click.Choice(_PRIORITIES), default=None, help="Priority label.")
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
@json_option
def sprint_item_list(
    status: str | None,
    project: str | None,
    project_id: str | None,
    sprint: str | None,
    as_json: bool,
) -> None:
    params = _drop_none({
        "status": status,
        "project": project,
        "project_id": project_id,
        "sprint_id": sprint_value_for_filter(sprint, as_json),
    })
    data = http.send("GET", "/api/items", as_json=as_json, params=params, request_actor="ordinary")
    http.emit(
        data,
        as_json,
        _lines(data["items"], lambda i: f"{i['id']} {i['status']} {i['priority']} {i['title']}"),
    )


@sprint_item.command("show")
@click.argument("item_id")
@json_option
def sprint_item_show(item_id: str, as_json: bool) -> None:
    data = http.send("GET", f"/api/items/{item_id}", as_json=as_json, request_actor="ordinary")
    http.emit(data, as_json, f"{data['id']} {data['status']} {data['priority']} {data['title']}")


@sprint_item.command("set")
@click.argument("item_id")
@click.argument("field", type=click.Choice(sorted(_ITEM_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option("--body-file", default=None, help="Read field text from this file, or - for stdin.")
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


@sprint_item.command("add-ticket")
@click.argument("item_id")
@click.argument("ticket_id")
@json_option
def sprint_item_add_ticket(item_id: str, ticket_id: str, as_json: bool) -> None:
    data = http.send(
        "POST",
        f"/api/items/{item_id}/tickets",
        as_json=as_json,
        json_body={"ticket_id": ticket_id},
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{ticket_id} added to {item_id}")


@sprint_item.command("remove-ticket")
@click.argument("item_id")
@click.argument("ticket_id")
@json_option
def sprint_item_remove_ticket(item_id: str, ticket_id: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        f"/api/items/{item_id}/tickets/{ticket_id}",
        as_json=as_json,
        request_actor="ordinary",
    )
    http.emit(data, as_json, f"{ticket_id} removed from {item_id}")


# --- chief --------------------------------------------------------------------


def _external_work_body(
    *,
    state: str,
    user_note_file: str,
    recap_file: str | None,
    success_file: str | None,
    approach_file: str | None,
    plan_file: str | None,
    result_file: str | None,
    as_json: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "state": state,
        "user_note": read_required_option_body(user_note_file, as_json, "user-note"),
    }
    for key, source in (
        ("recap", recap_file),
        ("success", success_file),
        ("approach", approach_file),
        ("plan", plan_file),
        ("result", result_file),
    ):
        if source is not None:
            body[key] = _read_source(source, as_json)
    return body


@main.group("chief")
def chief() -> None:
    """Import work already completed outside Panels."""


@chief.command("reconcile-ticket-from-external-work")
@click.argument("ticket_id")
@click.option("--state", required=True, type=click.Choice([state.value for state in STATE_ORDER]))
@click.option("--user-note-file", required=True, help="Complete resulting ticket note file, or -.")
@click.option("--recap-file", default=None, help="Read the recap from this file, or -.")
@click.option("--success-file", default=None, help="Read settled success from this file, or -.")
@click.option("--approach-file", default=None, help="Read settled approach from this file, or -.")
@click.option("--plan-file", default=None, help="Read settled plan from this file, or -.")
@click.option("--result-file", default=None, help="Read settled result from this file, or -.")
@json_option
def chief_reconcile_ticket_from_external_work(
    ticket_id: str,
    state: str,
    user_note_file: str,
    recap_file: str | None,
    success_file: str | None,
    approach_file: str | None,
    plan_file: str | None,
    result_file: str | None,
    as_json: bool,
) -> None:
    body = _external_work_body(
        state=state,
        user_note_file=user_note_file,
        recap_file=recap_file,
        success_file=success_file,
        approach_file=approach_file,
        plan_file=plan_file,
        result_file=result_file,
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
        f"{data['id']} external work reconciled {data['state']}",
    )


@chief.command("create-ticket-from-external-work")
@click.option("--title", required=True, help="Ticket title.")
@click.option("--state", required=True, type=click.Choice([state.value for state in STATE_ORDER]))
@click.option("--user-note-file", required=True, help="Complete resulting ticket note file, or -.")
@click.option("--recap-file", default=None, help="Read the recap from this file, or -.")
@click.option("--success-file", default=None, help="Read settled success from this file, or -.")
@click.option("--approach-file", default=None, help="Read settled approach from this file, or -.")
@click.option("--plan-file", default=None, help="Read settled plan from this file, or -.")
@click.option("--result-file", default=None, help="Read settled result from this file, or -.")
@click.option("--priority", type=click.Choice(_PRIORITIES), default=None, help="Priority label.")
@click.option("--deadline", default=None, help="Due date in YYYY-MM-DD form.")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--sprint", default=None, help="Sprint id, current, or none.")
@click.option("--sprint-item", "sprint_item", default=None, help="Parent sprint item id.")
@json_option
def chief_create_ticket_from_external_work(
    title: str,
    state: str,
    user_note_file: str,
    recap_file: str | None,
    success_file: str | None,
    approach_file: str | None,
    plan_file: str | None,
    result_file: str | None,
    priority: str | None,
    deadline: str | None,
    project: str | None,
    project_id: str | None,
    sprint: str | None,
    sprint_item: str | None,
    as_json: bool,
) -> None:
    body = _external_work_body(
        state=state,
        user_note_file=user_note_file,
        recap_file=recap_file,
        success_file=success_file,
        approach_file=approach_file,
        plan_file=plan_file,
        result_file=result_file,
        as_json=as_json,
    )
    body["title"] = title
    if priority is not None:
        body["priority"] = priority
    if deadline is not None:
        body["deadline"] = deadline
    add_project_selectors(
        body, project=project, project_id=project_id, required=False, as_json=as_json
    )
    if sprint is not None:
        body["sprint_id"] = sprint_value_for_write(sprint, as_json)
    if sprint_item is not None:
        body["sprint_item_id"] = sprint_item
    data = http.send(
        "POST",
        "/api/chief/tickets/from-external-work",
        as_json=as_json,
        json_body=body,
        request_actor="chief",
    )
    http.emit(data, as_json, f"{data['id']} external work created {data['state']}")


# --- worker -------------------------------------------------------------------


@main.group("worker")
def worker() -> None:
    """Worker-only ticket and item writes."""


@worker.command("my-ticket")
@json_option
def worker_my_ticket(as_json: bool) -> None:
    key = os.environ.get("HERMES_SESSION_KEY", "").strip()
    if not key:
        http.fail_validation(
            "no HERMES_SESSION_KEY in env; not running as a ticket worker", as_json
        )
    data = http.send("GET", f"/api/tickets/by-session/{key}", as_json=as_json)
    http.emit(data, as_json, f"{data['id']} {data['state']} {data['priority']} {data['title']}")


@worker.command("propose")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", required=True, help="Read proposal text from this file, or -.")
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


@worker.command("recap")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Read recap text from this file, or -.")
@json_option
def worker_recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("PUT", f"/api/tickets/{tid}/recap", as_json=as_json, json_body={"body": body})
    http.emit(data, as_json, f"recap written on {data['id']}")


@worker.command("note")
@click.argument("args", nargs=-1)
@click.option(
    "--body-file", default=None, help="Read field user guidance text from this file, or -."
)
@json_option
def worker_note(args: tuple[str, ...], body_file: str | None, as_json: bool) -> None:
    if len(args) == 1:
        ticket_id: str | None = None
        field = args[0]
    elif len(args) == 2:
        ticket_id = args[0]
        field = args[1]
    else:
        http.fail_validation("usage: worker note [ticket-id] <field>", as_json)
    if field not in _FIELDS:
        http.fail_validation("field must be success, approach, plan, or result", as_json)
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "PUT", f"/api/tickets/{tid}/notes/{field}", as_json=as_json, json_body={"user_note": body}
    )
    http.emit(data, as_json, f"user note {field} written on {data['id']}")


if __name__ == "__main__":
    main()
