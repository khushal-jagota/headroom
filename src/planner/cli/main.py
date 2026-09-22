"""The `panels` CLI.

The command tree mirrors the product model:

* send-message: send text to one Panels conversation owner
* day: plan and inspect a planning day
* ticket: create, inspect, organize, and approve tickets
* sprint: create, inspect, edit, and populate sprints and sprint items

Administrative and compatibility commands remain callable, but ordinary help exposes
only the five actor-neutral roots used for Panels work.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from planner.cli import http
from planner.cli.errors import PanelsGroup
from planner.cli.record_projection import (
    RecordPart,
    parse_part_names,
    part,
    project_record,
    render_text,
)
from planner.environments.cli import environment as environment_group
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.message_delivery.contracts import MessageDeliveryMode
from planner.worker_types.contracts import BRIEF_FIELD_ID

_PRIORITIES = ["P0", "P1", "P2", "P3"]
_TICKET_ID_ENV = "PLAN_TICKET_ID"
_ACTOR_ENV = "PLAN_ACTOR"
_SPRINT_ITEM_ID_ENV = "PLAN_SPRINT_ITEM_ID"

_DAY_FIELDS = {
    "focus": "focus",
    "brief-take": "brief_take",
    "watchout": "watchout",
    "if-today-lands": "if_today_lands",
    "midday-reconciliation": "midday_reconciliation",
    "notes": "notes",
}


# One way to name who holds a ceiling, wherever tooling takes one. The kind follows from
# the id, so the common case — hand this to Khushal — is `--holder me`.
_HOLDER_ALIASES = {
    "me": ("owner", "owner"),
    "owner": ("owner", "owner"),
    "chief": ("chief", "chief"),
}


def caller_principal() -> dict[str, str]:
    """The principal this process is, as a holder object.

    A caller who does not say where a ceiling goes next keeps it. Khushal keeps it as the
    owner, and a Sprint Item or a Ticket keeps it as itself, which is what the two approval
    doors each defaulted to before they became one.
    """
    actor = os.environ.get(_ACTOR_ENV, "").strip()
    ticket_id = os.environ.get(_TICKET_ID_ENV, "").strip()
    sprint_item_id = os.environ.get(_SPRINT_ITEM_ID_ENV, "").strip()
    if actor == "chief":
        return {"kind": "chief", "id": "chief"}
    if actor == "worker" and ticket_id:
        return {"kind": "ticket", "id": ticket_id}
    if actor == "sprint_item_supervisor" and sprint_item_id:
        return {"kind": "sprint_item", "id": sprint_item_id}
    return {"kind": "owner", "id": "owner"}


def resolve_holder(raw: str, as_json: bool) -> dict[str, str]:
    """Turn a holder word or id into the principal object the API takes."""
    holder = raw.strip()
    alias = _HOLDER_ALIASES.get(holder.lower())
    if alias is not None:
        return {"kind": alias[0], "id": alias[1]}
    if holder.startswith("si_"):
        return {"kind": "sprint_item", "id": holder}
    if holder.startswith("t_"):
        return {"kind": "ticket", "id": holder}
    http.fail_validation(
        "holder must be me, chief, a Sprint Item id, or a Ticket id",
        as_json,
    )
    raise AssertionError("unreachable")


_TICKET_SET_FIELDS = {
    "ceiling": "ceiling",
    "ceiling-holder": "ceiling_holder",
    "title": "title",
    "kickoff-note": "brief",
    "priority": "priority",
    "deadline": "deadline",
    "project": "project",
    "project-id": "project_id",
}

_SPRINT_FIELDS = {
    "name": "name",
    "date-start": "date_start",
    "date-end": "date_end",
    "primary-bet": "primary_bet",
    "kickoff": "kickoff",
    "checkpoint": "checkpoint",
    "review": "review",
}

_ITEM_FIELDS = {
    "title": "title",
    "body": "body",
    "priority": "priority",
    "deadline": "deadline",
    "project": "project",
    "project-id": "project_id",
}

_PROJECT_FIELDS = {
    "folder-path": "folder_path",
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


def _read_json_object(spec: str, as_json: bool, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_source(spec, as_json))
    except json.JSONDecodeError as exc:
        http.fail_validation(f"invalid {label} JSON: {exc.msg}", as_json)
    if not isinstance(value, dict):
        http.fail_validation(f"{label} must be one JSON object", as_json)
    return value


def read_worker_stdin_body(as_json: bool, label: str = "body") -> str:
    text = sys.stdin.read()
    if not text.strip():
        http.fail_validation(f"empty {label}", as_json)
    return text


def refuse_worker_body_file(
    body_file: str | None, as_json: bool, stdin_form: str
) -> None:
    if body_file is not None:
        http.fail_validation(
            f"--body-file is no longer supported: {stdin_form}", as_json
        )


def read_optional_body(body_file: str | None, as_json: bool) -> str | None:
    if body_file is None:
        return None
    return _read_source(body_file, as_json)


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


def _feedback_list_line(note: dict[str, Any]) -> str:
    captured_at = datetime.fromtimestamp(int(note["created_at"])).astimezone()
    page = note["page_label"] or "No page"
    return f"{note['id']} · {captured_at.strftime('%Y-%m-%d %H:%M')} · {page} · {note['text']}"


def _current_sprint_id(as_json: bool) -> str:
    data = http.send("GET", "/api/sprint/current", as_json=as_json)
    sprint = data.get("sprint") if isinstance(data, dict) else None
    if sprint is None:
        http.fail_validation(
            "no current sprint", as_json, recovery="panels sprint list"
        )
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
        projection = project_record(
            header, parts, _record_parts(raw_part_names, as_json)
        )
    except ValueError as exc:
        requested = () if raw_part_names is None else tuple(raw_part_names.split(","))
        if "rejection" in requested:
            http.fail_validation(
                str(exc),
                as_json,
                no_route=(
                    "Revision feedback arrives in the Worker prompt. "
                    "No panels command reads it."
                ),
            )
        http.fail_validation(str(exc), as_json)
    http.emit(projection, as_json, render_text(projection))


def _ticket_record(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, RecordPart]]:
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
        "ceiling",
    )
    header = {key: data[key] for key in header_keys if key in data}
    manifest = _worker_type(str(data["worker_type"]), True)
    parts = {
        field["id"]: part(data["field_values"].get(field["id"]))
        for field in manifest["fields"]
    }
    parts["proposal"] = part(data.get("pending_proposal"))
    parts["recap"] = part(data.get("recap", ""))
    parts["guidance"] = part(data.get("guidance", ""))
    return header, parts


def _sprint_record(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    header = {key: data[key] for key in ("id", "name", "date_start", "date_end")}
    part_names = ("primary_bet", "kickoff", "checkpoint", "review")
    return header, {name: part(data[name]) for name in part_names}


def _sprint_item_record(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    header_keys = (
        "id",
        "title",
        "committed_sprints",
        "project_id",
        "project",
        "priority",
        "deadline",
        "kind",
    )
    header = {key: data[key] for key in header_keys if key in data}
    # The Sprint Item's agent is part of the Sprint Item record. It used to be a read of its
    # own, which returned these four values and nothing this read did not already carry.
    supervisor = data.get("supervisor")
    if supervisor is not None:
        launch = supervisor["launch_configuration"]
        header |= {
            "supervisor_agent_key": supervisor["agent_key"],
            "supervisor_conversation_id": supervisor["conversation_id"],
            "employee_backend": launch["employee_backend"],
            "employee_launch_model": launch["employee_launch_model"],
            "employee_launch_reasoning_effort": launch[
                "employee_launch_reasoning_effort"
            ],
        }
    return (header, {"body": part(data["body"])})


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


def _project_record(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    return (
        {key: data[key] for key in ("id", "name", "priority", "folder_path")},
        {"summary": part(data["summary"])},
    )


# The manifest arrives as a plain JSON body across the HTTP boundary, so the CLI holds
# it as a plain dict. Keeping the transport shape local leaves CLI request construction
# independent of Worker-type interpretation.
def _worker_types(as_json: bool) -> dict[str, dict[str, Any]]:
    """The served manifests keyed by Worker type — the single source for Stage order /
    gates / fields / ceiling range per type (no parallel lifecycle encoding in the CLI).
    """
    data = http.send("GET", "/api/worker-types", as_json=as_json)
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


@click.group(cls=PanelsGroup)
def main() -> None:
    """Operate the local planner server."""


main.add_command(environment_group)
environment_group.hidden = True


# --- feedback -----------------------------------------------------------------


@main.group("feedback", hidden=True)
def feedback_group() -> None:
    """Read and use captured feedback notes."""


@feedback_group.command("list")
@json_option
def feedback_list(as_json: bool) -> None:
    result = http.send("GET", "/api/feedback", as_json=as_json)
    http.emit(
        result,
        as_json,
        _lines(
            result["open"],
            _feedback_list_line,
        ),
    )


@feedback_group.command("use")
@click.option(
    "--ticket", "ticket_id", required=True, help="Ticket receiving the notes."
)
@click.argument("feedback_ids", nargs=-1, required=True)
@json_option
def feedback_use(ticket_id: str, feedback_ids: tuple[str, ...], as_json: bool) -> None:
    result = http.send(
        "POST",
        "/api/feedback/use",
        as_json=as_json,
        json_body={"feedback_ids": list(feedback_ids), "ticket_id": ticket_id},
    )
    http.emit(
        result,
        as_json,
        f"{len(result['notes'])} feedback note(s) used on {ticket_id}",
    )


@main.command("send-message")
@click.option("--owner", is_flag=True, help="Send to Khushal.")
@click.option("--chief", is_flag=True, help="Send to the Chief of Staff.")
@click.option("--ticket", "ticket_id", default=None, help="Send to a Ticket worker.")
@click.option(
    "--sprint-item",
    "sprint_item_id",
    default=None,
    help="Send to a Sprint Item supervisor.",
)
@click.option(
    "--mode",
    type=click.Choice([mode.value for mode in MessageDeliveryMode]),
    default=MessageDeliveryMode.steer.value,
    show_default=True,
    help="Steer into the running turn, queue behind it, or interrupt it and send now.",
)
@click.option(
    "--message",
    default=None,
    help="Message text. Addressed agent turns must reply through Send Message.",
)
@click.option(
    "--body-file", default=None, help="Read message text from this file, or -."
)
@json_option
def send_message(
    owner: bool,
    chief: bool,
    ticket_id: str | None,
    sprint_item_id: str | None,
    mode: str,
    message: str | None,
    body_file: str | None,
    as_json: bool,
) -> None:
    """Send one message to one Panels conversation owner.

    An addressed agent turn must send one explicit reply before it completes.
    """
    targets = sum(
        1
        for selected in (
            owner,
            chief,
            ticket_id is not None,
            sprint_item_id is not None,
        )
        if selected
    )
    if targets != 1:
        http.fail_validation(
            "send-message requires exactly one of --owner, --chief, --ticket, or --sprint-item",
            as_json,
        )
    if (message is None) == (body_file is None):
        http.fail_validation(
            "send-message requires exactly one of --message or --body-file", as_json
        )
    text = message if message is not None else _read_source(body_file or "", as_json)
    if not text.strip():
        http.fail_validation("empty message", as_json)
    recipient: dict[str, str]
    if owner:
        recipient = {"kind": "owner", "id": "owner"}
    elif chief:
        recipient = {"kind": "chief", "id": "chief"}
    elif ticket_id is not None:
        recipient = {"kind": "ticket", "id": ticket_id}
    else:
        assert sprint_item_id is not None
        recipient = {"kind": "sprint_item", "id": sprint_item_id}
    data = http.send(
        "POST",
        "/api/messages/send",
        as_json=as_json,
        json_body={"target": recipient, "message": text, "mode": mode},
    )
    http.emit(data, as_json, f"message {data['fate']}")


@main.command("serve", hidden=True)
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


@main.command("restart", hidden=True)
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


@main.group("schedule", hidden=True)
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
    if backlog and sprint_id is not None:
        http.fail_validation("--backlog cannot be combined with --sprint", as_json)
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
    if backlog:
        body["sprint_id"] = None
        body["placement_mode"] = "backlog"
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
    )
    http.emit(
        result,
        as_json,
        f"{result['id']} {result['local_time']} {result['cadence']} {result['worker_type']}",
    )


@schedule_group.command("list")
@json_option
def schedule_list(as_json: bool) -> None:
    result = http.send("GET", "/api/schedules", as_json=as_json)
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
    )
    http.emit(result, as_json, f"{result['id']} {field} set")


# --- worker types -------------------------------------------------------------


@main.group("worker-type", hidden=True)
def worker_type_group() -> None:
    """Inspect and declare the Worker types."""


@worker_type_group.command("list")
@json_option
def worker_type_list(as_json: bool) -> None:
    data = http.send("GET", "/api/worker-types", as_json=as_json)
    http.emit(
        data,
        as_json,
        _lines(data["worker_types"], lambda item: str(item["worker_type"])),
    )


@worker_type_group.command("show")
@click.argument("worker_type")
@json_option
def worker_type_show(worker_type: str, as_json: bool) -> None:
    """Print one Worker type's stored record, in the shape `save` takes back."""
    data = http.send(
        "GET",
        f"/api/worker-types/{worker_type}",
        as_json=as_json,
    )
    http.emit(data, as_json, json.dumps(data, indent=2, ensure_ascii=False))


@worker_type_group.command("save")
@json_option
def worker_type_save(as_json: bool) -> None:
    """Declare a Worker type, or replace the one with this id. Record on stdin."""
    body = read_worker_stdin_body(as_json, "worker type record")
    try:
        record = json.loads(body)
    except ValueError:
        http.fail_validation("worker type record must be JSON", as_json)
    if not isinstance(record, dict):
        http.fail_validation("worker type record must be a JSON object", as_json)
    data = http.send(
        "POST",
        "/api/worker-types",
        json_body=record,
        as_json=as_json,
    )
    http.emit(data, as_json, f"{data['worker_type']} saved")


@worker_type_group.command("skill")
@click.argument("worker_type")
@click.option(
    "--description", required=True, help="The one-line description in the frontmatter."
)
@json_option
def worker_type_skill(worker_type: str, description: str, as_json: bool) -> None:
    """Replace this Worker type's skill. Markdown body on stdin."""
    body = read_worker_stdin_body(as_json, "skill body")
    data = http.send(
        "PUT",
        f"/api/workers/{worker_type}/skill",
        json_body={"description": description, "markdown_body": body},
        as_json=as_json,
    )
    http.emit(data, as_json, f"{data['specialist_skill']['name']} saved")


# --- project ------------------------------------------------------------------


@main.group("skill", hidden=True)
def skill_group() -> None:
    """Inspect the managed skills."""


@skill_group.command("check")
@json_option
def skill_check(as_json: bool) -> None:
    """Report every skill row naming a command, Stage or skill this build lacks."""
    data = http.send("GET", "/api/skills/stale", as_json=as_json)
    references = data["stale_references"]
    if not references:
        http.emit(data, as_json, "every skill row names only things this build has")
        return
    http.emit(
        data,
        as_json,
        "\n".join(
            f"{item['skill_name']}: {item['kind']} {item['reference']}"
            for item in references
        )
        + f"\n\n{len(data['skill_names'])} skills hold something this build does not have",
    )


@main.group("project")
def project_group() -> None:
    """List, inspect, create, and update projects."""


@project_group.command("list")
@bounded_options
@json_option
def project_list(limit: int, offset: int, as_json: bool) -> None:
    data = http.send(
        "GET",
        "/api/projects",
        as_json=as_json,
        params={"detail": "summary", "limit": limit, "offset": offset},
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
        "/api/projects",
        as_json=as_json,
        params={"detail": "full", "id": project_id},
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
@click.option(
    "--folder-path", default=None, help="Optional absolute Project folder path."
)
@json_option
def project_create(
    name: str,
    priority: str,
    summary: str | None,
    folder_path: str | None,
    as_json: bool,
) -> None:
    body: dict[str, Any] = {"name": name, "priority": priority}
    if summary is not None:
        body["summary"] = summary
    if folder_path is not None:
        body["folder_path"] = folder_path
    data = http.send(
        "POST",
        "/api/projects",
        as_json=as_json,
        json_body=body,
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
        json_body={
            api_field: (
                None
                if field == "folder-path" and new_value is None
                else "" if new_value is None else new_value
            )
        },
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
    data = http.send("GET", f"/api/day/{date_}", as_json=as_json)
    header, parts = _day_record(data)
    _emit_record(header, parts, part_names, as_json)


@day.command("list-tickets", hidden=True)
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@bounded_options
@json_option
def day_list_tickets(date_: str, limit: int, offset: int, as_json: bool) -> None:
    data = http.send(
        "GET",
        f"/api/day/{date_}/tickets",
        as_json=as_json,
        params={"limit": limit, "offset": offset},
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
    )
    http.emit(data, as_json, f"day {data['id']} {field} set")


@day.command("add-ticket")
@click.argument("ticket_id")
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@json_option
def day_add_ticket(ticket_id: str, date_: str, as_json: bool) -> None:
    data = http.send(
        "PUT",
        f"/api/collections/day_tickets/{date_}/{ticket_id}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{ticket_id} added to {data['container_id']}")


@day.command("remove-ticket")
@click.argument("ticket_id")
@click.option("--date", "date_", default="today", help="today or YYYY-MM-DD.")
@json_option
def day_remove_ticket(ticket_id: str, date_: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        f"/api/collections/day_tickets/{date_}/{ticket_id}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{ticket_id} removed from {data['container_id']}")


# --- ticket -------------------------------------------------------------------


@main.group("ticket")
def ticket() -> None:
    """Create, inspect, organize, and approve tickets."""


@ticket.command("create")
@click.option(
    "--input-json",
    default=None,
    help="Read one Ticket create object from this file, or - for standard input.",
)
@click.option("--title", default=None, hidden=True)
@click.option(
    "--worker-type",
    "worker_type",
    default=None,
    hidden=True,
)
@click.option("--employee-backend", default=None, hidden=True)
@click.option(
    "--employee-launch-model",
    default=None,
    hidden=True,
)
@click.option("--priority", type=click.Choice(_PRIORITIES), default=None, hidden=True)
@click.option("--deadline", default=None, hidden=True)
@click.option("--project", default=None, hidden=True)
@click.option("--project-id", default=None, hidden=True)
@click.option("--sprint", "sprint_id", default=None, hidden=True)
@click.option("--sprint-item", "sprint_item", default=None, hidden=True)
@click.option("--backlog", is_flag=True, default=False, hidden=True)
@click.option(
    "--blocked-by",
    "blocked_by_ticket_ids",
    multiple=True,
    hidden=True,
)
@click.option("--kickoff-note", default=None, hidden=True)
@click.option(
    "--kickoff-note-file",
    default=None,
    hidden=True,
)
@click.option("--ceiling", default=None, hidden=True)
@click.option("--holder", default=None, hidden=True)
@json_option
def ticket_create(
    input_json: str | None,
    title: str | None,
    worker_type: str | None,
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
    ceiling: str | None,
    holder: str | None,
    as_json: bool,
) -> None:
    """Create one Ticket from one JSON object through the existing atomic writer."""
    legacy_values = (
        title,
        worker_type,
        employee_backend,
        employee_launch_model,
        priority,
        deadline,
        project,
        project_id,
        sprint_id,
        sprint_item,
        kickoff_note,
        kickoff_note_file,
        ceiling,
        holder,
    )
    legacy_used = (
        any(value is not None for value in legacy_values)
        or backlog
        or bool(blocked_by_ticket_ids)
    )
    if input_json is not None:
        if legacy_used:
            http.fail_validation(
                "--input-json cannot be combined with legacy Ticket create options",
                as_json,
            )
        body = _read_json_object(input_json, as_json, "Ticket create input")
        data = http.send("POST", "/api/tickets", as_json=as_json, json_body=body)
        http.emit(data, as_json, f"{data['id']} {data['stage']}")
        return
    if title is None or worker_type is None:
        http.fail_validation(
            "Ticket creation requires --input-json "
            "(legacy calls require --title and --worker-type)",
            as_json,
        )
    body: dict[str, Any] = {"title": title, "worker_type": worker_type}
    if ceiling is not None:
        body["ceiling"] = ceiling
    if holder is not None:
        body["ceiling_holder"] = resolve_holder(holder, as_json)
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
    if backlog and sprint_id is not None:
        http.fail_validation("--backlog cannot be combined with --sprint", as_json)
    if sprint_item is not None:
        body["sprint_item_id"] = sprint_item
    if backlog:
        body["sprint_id"] = None
    if blocked_by_ticket_ids:
        body["blocked_by_ticket_ids"] = list(blocked_by_ticket_ids)
    data = http.send(
        "POST",
        "/api/tickets",
        as_json=as_json,
        json_body=body,
    )
    http.emit(data, as_json, f"{data['id']} {data['stage']}")


@ticket.command("show")
@click.argument("ticket_id", required=False)
@click.argument("part_names", required=False)
@json_option
def ticket_show(ticket_id: str | None, part_names: str | None, as_json: bool) -> None:
    """Read the current Worker's Ticket, or read a named Ticket."""
    named_ticket = ticket_id is not None and ticket_id.startswith("t_")
    if ticket_id is not None and not named_ticket:
        if part_names is not None:
            http.fail_validation(
                "a current-Ticket part list cannot be followed by another argument",
                as_json,
            )
        part_names = ticket_id
        ticket_id = None
    tid = resolve_ticket_id(ticket_id, as_json)
    if not named_ticket:
        data = http.send("GET", f"/api/tickets/{tid}/worker-self", as_json=as_json)
    else:
        data = http.send(
            "GET",
            "/api/tickets",
            as_json=as_json,
            params={"detail": "full", "id": tid},
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
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Delete even a ticket that still looks running, for a stuck ticket status.",
)
@json_option
def ticket_delete(ticket_id: str | None, yes: bool, force: bool, as_json: bool) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    if not yes:
        http.fail_validation("permanent deletion requires --yes", as_json)
    data = http.send(
        "DELETE",
        f"/api/tickets/{tid}",
        as_json=as_json,
        params={"force": True} if force else None,
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
    help="Include done Tickets.",
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
        "/api/tickets",
        as_json=as_json,
        params={"detail": "summary", **params},
    )
    http.emit(
        data,
        as_json,
        _format_bounded(_format_ticket_summaries(data["tickets"]), data["page"]),
    )


@ticket.command("edit")
@click.argument("ticket_id", required=False)
@click.option(
    "--input-json",
    required=True,
    help="Read one Ticket edit object from this file, or - for standard input.",
)
@json_option
def ticket_edit(ticket_id: str | None, input_json: str, as_json: bool) -> None:
    """Apply one or more Ticket field edits through the existing atomic writer."""
    tid = resolve_ticket_id(ticket_id, as_json)
    body = _read_json_object(input_json, as_json, "Ticket edit input")
    if not body:
        http.fail_validation(
            "Ticket edit input must contain at least one field", as_json
        )
    data = http.send("PATCH", f"/api/tickets/{tid}", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} edited")


@ticket.command("set", hidden=True)
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
    if (
        field in {"title", "priority", "ceiling", "ceiling-holder"}
        and new_value is None
    ):
        http.fail_validation(f"{field} cannot be cleared", as_json)
    if field == "priority" and new_value not in _PRIORITIES:
        http.fail_validation("priority must be P0, P1, P2, or P3", as_json)
    if field == "kickoff-note" and new_value is None:
        new_value = ""
    body: dict[str, Any]
    if field == "kickoff-note":
        body = {"field_values": {BRIEF_FIELD_ID: new_value}}
    elif field == "ceiling-holder":
        body = {api_field: resolve_holder(str(new_value), as_json)}
    else:
        body = {api_field: new_value}
    data = http.send(
        "PATCH",
        f"/api/tickets/{ticket_id}",
        as_json=as_json,
        json_body=body,
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


@ticket.command("complete")
@click.argument("ticket_id")
@click.argument("field")
@click.option("--value", default=None, help="Complete the gate with this value.")
@click.option(
    "--body-file", default=None, help="Read the value from this file, or - for stdin."
)
@json_option
def ticket_complete(
    ticket_id: str,
    field: str,
    value: str | None,
    body_file: str | None,
    as_json: bool,
) -> None:
    """Do a user-owned Stage's work yourself. The Stage then advances."""
    body = read_value_or_file(value, body_file, False, as_json, field)
    data = http.send(
        "POST",
        f"/api/tickets/{ticket_id}/complete/{field}",
        as_json=as_json,
        json_body={"body": body},
    )
    http.emit(data, as_json, f"{data['id']} {field} completed")


@ticket.command("set-value", hidden=True)
@click.argument("ticket_id")
@click.argument("field")
@click.option("--value", default=None, help="Set the field to this value.")
@click.option(
    "--body-file", default=None, help="Read field text from this file, or - for stdin."
)
@json_option
def ticket_set_value(
    ticket_id: str,
    field: str,
    value: str | None,
    body_file: str | None,
    as_json: bool,
) -> None:
    """Correct a value the Ticket has already passed."""
    body = read_value_or_file(value, body_file, False, as_json, field)
    data = http.send(
        "PATCH",
        f"/api/tickets/{ticket_id}",
        as_json=as_json,
        json_body={"field_values": {field: body}},
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


@ticket.command("place", hidden=True)
@click.argument("ticket_id")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--sprint", default=None, help="Sprint id or current.")
@click.option("--backlog", is_flag=True, default=False, help="Clear Sprint placement.")
@click.option(
    "--sprint-item", default=None, help="Optional Sprint Item classification."
)
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
    )
    http.emit(data, as_json, f"{data['id']} placement set")


@ticket.command("employee-configuration", hidden=True)
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
    )
    http.emit(
        data,
        as_json,
        f"{data['id']} employee configuration set {backend} {model}",
    )


@ticket.command("approve", hidden=True)
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--ceiling", default=None, help="Next ceiling stage or none.")
@click.option("--edit-file", default=None, help="Edited accepted body, or - for stdin.")
@click.option(
    "--holder",
    default=None,
    help="Who holds the next ceiling: me, chief, a Sprint Item id, or a Ticket id. "
    "Absent, you keep it.",
)
@click.option("--kickoff-title", default=None, help="Edited Kickoff title.")
@click.option(
    "--kickoff-note-file", default=None, help="Edited Kickoff note, or - for stdin."
)
@json_option
def ticket_approve(
    ticket_id: str | None,
    ceiling: str | None,
    edit_file: str | None,
    holder: str | None,
    kickoff_title: str | None,
    kickoff_note_file: str | None,
    as_json: bool,
) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    detail = http.send(
        "GET",
        "/api/tickets",
        as_json=as_json,
        params={"detail": "full", "id": tid},
    )
    stage = detail["stage"]
    manifest = _worker_type(detail["worker_type"], as_json)
    field = _gating_field_for_stage(manifest, stage)
    if field is None:
        http.fail_validation(
            f"ticket in {stage} has nothing to approve",
            as_json,
            no_route="No panels approval call is available for this Ticket.",
        )
    proposal = detail["pending_proposal"]
    if proposal is None or proposal["field"] != field:
        http.fail_validation(
            f"no pending {field} proposal",
            as_json,
            no_route="No panels approval call is available until a proposal exists.",
        )
    if ceiling is None:
        http.fail_validation("approval requires --ceiling", as_json)
    if kickoff_title is not None:
        if field != BRIEF_FIELD_ID:
            http.fail_validation(
                "--kickoff-title only applies while approving the brief", as_json
            )
        http.send(
            "PATCH",
            f"/api/tickets/{tid}",
            as_json=as_json,
            json_body={"title": kickoff_title},
        )
    field_payload: dict[str, Any] = {
        "next_ceiling": ceiling,
        "next_holder": (
            resolve_holder(holder, as_json)
            if holder is not None
            else caller_principal()
        ),
    }
    if edit_file is not None:
        field_payload["edited_body"] = _read_source(edit_file, as_json)
    if kickoff_note_file is not None:
        if field != BRIEF_FIELD_ID:
            http.fail_validation(
                "--kickoff-note-file only applies while approving the brief", as_json
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
    )
    http.emit(data, as_json, f"{data['id']} approved {field}")


@ticket.command("reject", hidden=True)
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--message", default=None, help="Focused revision guidance.")
@click.option(
    "--body-file", default=None, help="Read revision guidance from this file, or -."
)
@json_option
def ticket_reject(
    ticket_id: str | None, message: str | None, body_file: str | None, as_json: bool
) -> None:
    """Reject the proposal parked on this Ticket, with focused guidance."""
    tid = resolve_ticket_id(ticket_id, as_json)
    if (message is None) == (body_file is None):
        http.fail_validation(
            "reject requires exactly one of --message or --body-file", as_json
        )
    text = message if message is not None else _read_source(body_file or "", as_json)
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/reject",
        as_json=as_json,
        json_body={"message": text},
    )
    http.emit(data, as_json, f"{tid} proposal rejected")


@ticket.command("history")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--limit", type=click.IntRange(1, 100), default=30, show_default=True)
@click.option(
    "--before", type=int, default=None, help="Read the page before this sequence."
)
@click.option(
    "--after",
    type=int,
    default=None,
    help="Read forwards from this sequence instead of back from the end.",
)
@json_option
def ticket_history(
    ticket_id: str | None,
    limit: int,
    before: int | None,
    after: int | None,
    as_json: bool,
) -> None:
    """Read this Ticket's current Worker conversation."""
    tid = resolve_ticket_id(ticket_id, as_json)
    if after is not None and before is not None:
        http.fail_validation(
            "history reads one direction: --after or --before", as_json
        )
    detail = http.send(
        "GET",
        "/api/tickets",
        as_json=as_json,
        params={"detail": "full", "id": tid},
    )
    conversation_id = detail["conversation_id"]
    if not conversation_id:
        http.fail_validation(
            "the Ticket has no current Worker conversation",
            as_json,
            no_route="No panels history call is available until a Worker conversation exists.",
        )
    params = (
        {"after": after, "limit": limit}
        if after is not None
        else _drop_none({"limit": limit, "before": before})
    )
    page = http.send(
        "GET",
        f"/api/conversation/conversations/{conversation_id}/events",
        as_json=as_json,
        params=params,
    )
    data = {
        "ticket_id": tid,
        "conversation_id": conversation_id,
        "events": page["events"],
        "has_more": page["has_more"],
    }
    http.emit(data, as_json, f"{tid} {len(data['events'])} history events")


@ticket.command("restart-worker")
@click.argument("ticket_id")
@click.option(
    "--backend", default=None, help="Registered employee backend to restart on."
)
@click.option(
    "--model", default=None, help="Model that backend runs this Ticket's worker on."
)
@click.option(
    "--reasoning-effort",
    default=None,
    help="Reasoning effort for that model; leave it out for a model that takes none.",
)
@json_option
def ticket_restart_worker(
    ticket_id: str,
    backend: str | None,
    model: str | None,
    reasoning_effort: str | None,
    as_json: bool,
) -> None:
    """Start this Ticket's worker step again, on a dead Worker.

    Leave the options out to restart on what the Ticket already launches. Naming a
    configuration changes what this Ticket launches on from now on, so a Worker that died
    on its backend does not come back on the same one. Backend and model go together,
    because a model id belongs to the backend that named it.
    """
    if (backend is None) != (model is None):
        http.fail_validation(
            "restart-worker needs --backend and --model together, or neither", as_json
        )
    body: dict[str, Any] = {}
    if backend is not None:
        body = {
            "employee_backend": backend,
            "employee_launch_model": model,
            "employee_launch_reasoning_effort": reasoning_effort,
        }
    data = http.send(
        "POST",
        f"/api/tickets/{ticket_id}/restart-worker",
        as_json=as_json,
        json_body=body,
    )
    configuration = data["employee_configuration"]
    launch = (
        f"{configuration['employee_backend']} {configuration['employee_launch_model']}"
    )
    http.emit(
        data,
        as_json,
        (
            f"{ticket_id} restarted on {launch}"
            if data["started"]
            else f"{ticket_id} did not start: {data['not_started_because']}"
        ),
    )


@ticket.command("block")
@click.argument("ticket_id")
@click.option("--by", "blocker_id", required=True, help="Blocking ticket id.")
@json_option
def ticket_block(ticket_id: str, blocker_id: str, as_json: bool) -> None:
    data = http.send(
        "PUT",
        f"/api/collections/blockers/{ticket_id}/{blocker_id}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{ticket_id} blocked by {blocker_id}")


@ticket.command("unblock")
@click.argument("ticket_id")
@click.option("--by", "blocker_id", required=True, help="Blocking ticket id.")
@json_option
def ticket_unblock(ticket_id: str, blocker_id: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        f"/api/collections/blockers/{ticket_id}/{blocker_id}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{ticket_id} unblocked from {blocker_id}")


@ticket.command("copy", hidden=True)
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def ticket_copy(ticket_id: str | None, as_json: bool) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    text = http.send_text(
        "GET",
        f"/api/tickets/{tid}/copy-text",
        as_json=as_json,
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
@click.option("--primary-bet", default="", help="Short sprint summary.")
@click.option("--kickoff", default="", help="Kickoff document.")
@click.option("--checkpoint", default="", help="Checkpoint document.")
@click.option("--review", default="", help="Sprint review document.")
@json_option
def sprint_create(
    name: str,
    date_start: str,
    date_end: str,
    primary_bet: str,
    kickoff: str,
    checkpoint: str,
    review: str,
    as_json: bool,
) -> None:
    body = {
        "name": name,
        "date_start": date_start,
        "date_end": date_end,
        "primary_bet": primary_bet,
        "kickoff": kickoff,
        "checkpoint": checkpoint,
        "review": review,
    }
    data = http.send(
        "POST",
        "/api/sprints",
        as_json=as_json,
        json_body=body,
    )
    http.emit(data, as_json, f"{data['id']} {data['name']}")


@sprint.command("list")
@bounded_options
@json_option
def sprint_list(limit: int, offset: int, as_json: bool) -> None:
    data = http.send(
        "GET",
        "/api/sprints",
        as_json=as_json,
        params={"detail": "summary", "limit": limit, "offset": offset},
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
def sprint_show(sprint_id: str | None, part_names: str | None, as_json: bool) -> None:
    """Show a Sprint manifest or selected comma-separated PART_NAMES."""
    if sprint_id is None or sprint_id == "current":
        current_response = http.send("GET", "/api/sprint/current", as_json=as_json)
        data = current_response["sprint"]
        if data is None:
            http.fail_validation(
                "no current sprint", as_json, recovery="panels sprint list"
            )
    else:
        data = http.send(
            "GET",
            "/api/sprints",
            as_json=as_json,
            params={"detail": "full", "id": sprint_id},
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
@json_option
def sprint_item_create(
    title: str,
    project: str | None,
    project_id: str | None,
    body_file: str | None,
    priority: str | None,
    deadline: str | None,
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
    data = http.send("POST", "/api/items", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']}")


@sprint_item.command("list")
@click.option("--search", default=None, help="Find a Sprint Item by title.")
@click.option("--project", default=None, help="Only show project name.")
@click.option("--project-id", default=None, help="Only show project id.")
@bounded_options
@json_option
def sprint_item_list(
    search: str | None,
    project: str | None,
    project_id: str | None,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    params = _drop_none(
        {
            "search": search,
            "project": project,
            "project_id": project_id,
            "limit": limit,
            "offset": offset,
        }
    )
    data = http.send(
        "GET",
        "/api/items",
        as_json=as_json,
        params={"detail": "summary", **params},
    )
    http.emit(
        data,
        as_json,
        _format_bounded(
            _lines(
                data["items"],
                lambda i: f"{i['id']} {i['priority']} {i['title']}",
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
        "GET",
        "/api/items",
        as_json=as_json,
        params={"detail": "full", "id": item_id},
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
    data = http.send("DELETE", f"/api/items/{item_id}", as_json=as_json)
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
    data = http.send(
        "PATCH",
        f"/api/items/{item_id}",
        as_json=as_json,
        json_body={api_field: new_value},
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


@sprint_item.command("add-ticket")
@click.argument("item_id")
@click.argument("ticket_id")
@json_option
def sprint_item_add_ticket(item_id: str, ticket_id: str, as_json: bool) -> None:
    data = http.send(
        "PUT",
        f"/api/collections/outcome_tickets/{item_id}/{ticket_id}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{ticket_id} added to {item_id}")


@sprint_item.command("remove-ticket")
@click.argument("item_id")
@click.argument("ticket_id")
@json_option
def sprint_item_remove_ticket(item_id: str, ticket_id: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        f"/api/collections/outcome_tickets/{item_id}/{ticket_id}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{ticket_id} removed from {item_id}")


@sprint_item.command("workspace")
@click.argument("item_id")
@json_option
def sprint_item_workspace(item_id: str, as_json: bool) -> None:
    """Read this Sprint Item, its artifacts, and its current child Tickets."""
    data = http.send("GET", f"/api/items/{item_id}/workspace", as_json=as_json)
    http.emit(data, as_json, f"{item_id} {len(data['tickets'])} current Tickets")


# The Sprint Item's artifact subdirectory. These commands count paths from it, while the
# route counts from the Sprint Item root, so the prefix is added here.
_ARTIFACTS_DIRECTORY = "artifacts"


@sprint_item.group("artifact")
def sprint_item_artifact() -> None:
    """Read and write a Sprint Item's own artifacts."""


@sprint_item_artifact.command("list")
@click.argument("item_id")
@json_option
def sprint_item_artifact_list(item_id: str, as_json: bool) -> None:
    data = http.send("GET", f"/api/items/{item_id}/workspace", as_json=as_json)
    prefix = f"{_ARTIFACTS_DIRECTORY}/"
    artifacts = sorted(
        str(entry["path"])[len(prefix) :]
        for entry in data["artifacts"]
        if str(entry["path"]).startswith(prefix)
    )
    http.emit(
        {"sprint_item_id": item_id, "artifacts": artifacts},
        as_json,
        _lines(artifacts, str),
    )


@sprint_item_artifact.command("write")
@click.argument("item_id")
@click.argument("artifact_path")
@click.option("--body-file", required=True)
@json_option
def sprint_item_artifact_write(
    item_id: str, artifact_path: str, body_file: str, as_json: bool
) -> None:
    data = http.send(
        "PUT",
        f"/files/sprint-items/{item_id}/{_ARTIFACTS_DIRECTORY}/{artifact_path}",
        as_json=as_json,
        json_body={"content": _read_source(body_file, as_json)},
    )
    http.emit(data, as_json, data["url"])


@sprint_item_artifact.command("delete")
@click.argument("item_id")
@click.argument("artifact_path")
@json_option
def sprint_item_artifact_delete(
    item_id: str, artifact_path: str, as_json: bool
) -> None:
    data = http.send(
        "DELETE",
        f"/files/sprint-items/{item_id}/{_ARTIFACTS_DIRECTORY}/{artifact_path}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{artifact_path} deleted")


@sprint_item.group("conversation", hidden=True)
def sprint_item_conversation() -> None:
    """Compatibility and recovery paths for a Sprint Item supervisor conversation."""


@sprint_item_conversation.command("send")
@click.argument("item_id")
@click.option("--message", default=None, help="Message text.")
@click.option(
    "--body-file", default=None, help="Read message text from this file, or -."
)
@json_option
def sprint_item_conversation_send(
    item_id: str, message: str | None, body_file: str | None, as_json: bool
) -> None:
    if (message is None) == (body_file is None):
        http.fail_validation(
            "send requires exactly one of --message or --body-file", as_json
        )
    text = message if message is not None else _read_source(body_file or "", as_json)
    data = http.send(
        "POST",
        f"/api/items/{item_id}/conversation/send",
        as_json=as_json,
        json_body={"content": [{"piece": "text", "text": text}], "sender_label": "You"},
    )
    http.emit(data, as_json, f"message {data['fate']}")


@sprint_item_conversation.command("reset")
@click.argument("item_id")
@json_option
def sprint_item_conversation_reset(item_id: str, as_json: bool) -> None:
    data = http.send(
        "POST", f"/api/items/{item_id}/conversation/reset", as_json=as_json
    )
    http.emit(data, as_json, f"{item_id} supervisor conversation reset")


# --- worker -------------------------------------------------------------------


@main.group("worker", hidden=True)
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
            no_route=(
                "No panels worker call is available until the process has a Ticket identity."
            ),
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
    "--body-file", default=None, help="Removed: pipe proposal text on stdin instead."
)
@click.option("--recap", default=None, help="Removed: use `worker recap` instead.")
@click.option("--recap-file", default=None, help="Removed: use `worker recap` instead.")
@json_option
def worker_propose(
    ticket_id: str | None,
    body_file: str | None,
    recap: str | None,
    recap_file: str | None,
    as_json: bool,
) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    refuse_worker_body_file(body_file, as_json, "pipe proposal text on stdin instead")
    if recap is not None or recap_file is not None:
        http.fail_validation(
            "a proposal no longer carries the recap: use `panels worker recap` instead",
            as_json,
        )
    body = read_worker_stdin_body(as_json, "proposal body")
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/propose",
        as_json=as_json,
        json_body={"body": body},
    )
    http.emit(data, as_json, f"proposed on {data['id']}")


@worker.command("request-help")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--owner", is_flag=True, help="Ask Khushal for help.")
@click.option("--chief", is_flag=True, help="Ask the Chief of Staff for help.")
@click.option(
    "--ticket", "recipient_ticket_id", default=None, help="Ask another Ticket worker."
)
@click.option(
    "--sprint-item",
    "recipient_sprint_item_id",
    default=None,
    help="Ask a Sprint Item supervisor.",
)
@json_option
def worker_request_help(
    ticket_id: str | None,
    owner: bool,
    chief: bool,
    recipient_ticket_id: str | None,
    recipient_sprint_item_id: str | None,
    as_json: bool,
) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    selected = sum(
        int(value)
        for value in (
            owner,
            chief,
            recipient_ticket_id is not None,
            recipient_sprint_item_id is not None,
        )
    )
    if selected > 1:
        http.fail_validation("request-help accepts at most one recipient", as_json)
    recipient = None
    if owner:
        recipient = {"kind": "owner", "id": "owner"}
    elif chief:
        recipient = {"kind": "chief", "id": "chief"}
    elif recipient_ticket_id is not None:
        recipient = {"kind": "ticket", "id": recipient_ticket_id}
    elif recipient_sprint_item_id is not None:
        recipient = {"kind": "sprint_item", "id": recipient_sprint_item_id}
    message = read_worker_stdin_body(as_json, "help message")
    body: dict[str, Any] = {"message": message}
    if recipient is not None:
        body["recipient"] = recipient
    data = http.send(
        "POST", f"/api/tickets/{tid}/request-help", as_json=as_json, json_body=body
    )
    http.emit(data, as_json, f"help message {data['fate']}")


@worker.command("recap")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option(
    "--body-file", default=None, help="Removed: pipe recap text on stdin instead."
)
@json_option
def worker_recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    refuse_worker_body_file(body_file, as_json, "pipe recap text on stdin instead")
    body = read_worker_stdin_body(as_json, "recap")
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "PATCH", f"/api/tickets/{tid}", as_json=as_json, json_body={"recap": body}
    )
    http.emit(data, as_json, f"recap written on {data['id']}")


@worker.command("note")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option(
    "--append", "append_note", is_flag=True, help="Append to Ticket guidance."
)
@json_option
def worker_note(ticket_id: str | None, append_note: bool, as_json: bool) -> None:
    """Write Ticket guidance from stdin; no field selection is needed."""
    tid = resolve_ticket_id(ticket_id, as_json)
    body = sys.stdin.read()
    key = "guidance_append" if append_note else "guidance"
    data = http.send(
        "PATCH",
        f"/api/tickets/{tid}",
        as_json=as_json,
        json_body={key: body},
    )
    http.emit(data, as_json, f"guidance written on {data['id']}")


@ticket.command("proposal")
@click.argument("proposal_args", nargs=-1, metavar="[TICKET_ID] ACTION")
@click.option("--ceiling", default=None, help="Next ceiling for an accepted proposal.")
@click.option(
    "--holder", default=None, help="Next ceiling holder for an accepted proposal."
)
@click.option("--edit-file", default=None, help="Edited accepted body, or - for stdin.")
@click.option("--kickoff-title", default=None, help="Edited Kickoff title.")
@click.option(
    "--kickoff-note-file", default=None, help="Edited Kickoff note, or - for stdin."
)
@json_option
def ticket_proposal(
    proposal_args: tuple[str, ...],
    ceiling: str | None,
    holder: str | None,
    edit_file: str | None,
    kickoff_title: str | None,
    kickoff_note_file: str | None,
    as_json: bool,
) -> None:
    """Submit, accept, or revise the current Ticket proposal."""
    if len(proposal_args) == 1:
        ticket_id, action = None, proposal_args[0]
    elif len(proposal_args) == 2:
        ticket_id, action = proposal_args
    else:
        http.fail_validation(
            "proposal requires ACTION, with an optional Ticket id before it", as_json
        )
    if action not in {"submit", "accept", "revise"}:
        http.fail_validation("ACTION must be submit, accept, or revise", as_json)
    accept_options = (ceiling, holder, edit_file, kickoff_title, kickoff_note_file)
    if action != "accept" and any(value is not None for value in accept_options):
        http.fail_validation(
            f"{action} does not accept proposal approval options", as_json
        )
    if action == "submit":
        worker_propose.callback(ticket_id, None, None, None, as_json)
        return
    if action == "revise":
        ticket_reject.callback(ticket_id, None, "-", as_json)
        return
    ticket_approve.callback(
        ticket_id,
        ceiling,
        edit_file,
        holder,
        kickoff_title,
        kickoff_note_file,
        as_json,
    )


@ticket.command("request-help")
@click.argument("ticket_id", required=False)
@click.option("--owner", is_flag=True, help="Ask Khushal for help.")
@click.option("--chief", is_flag=True, help="Ask the Chief of Staff for help.")
@click.option(
    "--ticket", "recipient_ticket_id", default=None, help="Ask another Worker."
)
@click.option(
    "--sprint-item", "recipient_sprint_item_id", default=None, help="Ask a supervisor."
)
@json_option
def ticket_request_help(
    ticket_id: str | None,
    owner: bool,
    chief: bool,
    recipient_ticket_id: str | None,
    recipient_sprint_item_id: str | None,
    as_json: bool,
) -> None:
    """Record that this Ticket needs help and send the stdin message to one recipient."""
    worker_request_help.callback(
        ticket_id,
        owner,
        chief,
        recipient_ticket_id,
        recipient_sprint_item_id,
        as_json,
    )


@sprint.group("outcome", hidden=True)
def sprint_outcome() -> None:
    """Compatibility paths for Sprint Item membership."""


def _commit_outcome(
    method: str, sprint_id: str, outcome_id: str, as_json: bool
) -> None:
    sid = sprint_value_for_write(sprint_id, as_json)
    if sid is None:
        http.fail_validation("a commitment requires a Sprint", as_json)
    result = http.send(
        method,
        f"/api/collections/sprint_outcomes/{sid}/{outcome_id}",
        as_json=as_json,
    )
    http.emit(result, as_json, f"{outcome_id} commitment updated for {sid}")


@sprint_outcome.command("add")
@click.argument("sprint_id")
@click.argument("outcome_id")
@json_option
def sprint_outcome_add(sprint_id: str, outcome_id: str, as_json: bool) -> None:
    _commit_outcome("PUT", sprint_id, outcome_id, as_json)


@sprint_outcome.command("remove")
@click.argument("sprint_id")
@click.argument("outcome_id")
@json_option
def sprint_outcome_remove(sprint_id: str, outcome_id: str, as_json: bool) -> None:
    _commit_outcome("DELETE", sprint_id, outcome_id, as_json)


@sprint_outcome.command("list")
@click.argument("sprint_id")
@json_option
def sprint_outcome_list(sprint_id: str, as_json: bool) -> None:
    sid = sprint_value_for_write(sprint_id, as_json)
    if sid is None:
        http.fail_validation("tracking requires a Sprint", as_json)
    result = http.send("GET", f"/api/sprints/{sid}/tracking", as_json=as_json)
    http.emit(
        result,
        as_json,
        _lines(
            result["outcome_groups"],
            lambda group: f"{group['outcome']['id']} {group['outcome']['title']}",
        ),
    )


@sprint_outcome.command("carry")
@click.argument("source_sprint_id")
@click.argument("outcome_id")
@click.option("--to", "target_sprint_id", required=True)
@click.option(
    "--ticket",
    "ticket_ids",
    multiple=True,
    help="Exact unfinished Ticket to carry; repeat to select more.",
)
@json_option
def sprint_outcome_carry(
    source_sprint_id: str,
    outcome_id: str,
    target_sprint_id: str,
    ticket_ids: tuple[str, ...],
    as_json: bool,
) -> None:
    source = sprint_value_for_write(source_sprint_id, as_json)
    target = sprint_value_for_write(target_sprint_id, as_json)
    if source is None or target is None:
        http.fail_validation("carry requires source and target Sprints", as_json)
    result = http.send(
        "POST",
        f"/api/sprints/{source}/outcomes/{outcome_id}/carry",
        as_json=as_json,
        json_body={"target_sprint_id": target, "ticket_ids": list(ticket_ids)},
    )
    http.emit(
        result,
        as_json,
        f"{outcome_id} committed to {target}; {len(ticket_ids)} Tickets selected",
    )


@sprint.command("add-item")
@click.argument("sprint_id")
@click.argument("item_id")
@json_option
def sprint_add_item(sprint_id: str, item_id: str, as_json: bool) -> None:
    """Add one Sprint Item to a Sprint without moving its history."""
    _commit_outcome("PUT", sprint_id, item_id, as_json)


@sprint.command("remove-item")
@click.argument("sprint_id")
@click.argument("item_id")
@json_option
def sprint_remove_item(sprint_id: str, item_id: str, as_json: bool) -> None:
    """Remove one Sprint Item from a Sprint without moving its history."""
    _commit_outcome("DELETE", sprint_id, item_id, as_json)


@sprint.command("list-items")
@click.argument("sprint_id")
@json_option
def sprint_list_items(sprint_id: str, as_json: bool) -> None:
    """List the Sprint Items committed to one Sprint."""
    sprint_outcome_list.callback(sprint_id, as_json)


@sprint.command("carry-item")
@click.argument("source_sprint_id")
@click.argument("item_id")
@click.option("--to", "target_sprint_id", required=True)
@click.option(
    "--ticket",
    "ticket_ids",
    multiple=True,
    help="Exact unfinished Ticket to carry; repeat to select more.",
)
@json_option
def sprint_carry_item(
    source_sprint_id: str,
    item_id: str,
    target_sprint_id: str,
    ticket_ids: tuple[str, ...],
    as_json: bool,
) -> None:
    """Carry one Sprint Item and selected unfinished Tickets to another Sprint."""
    sprint_outcome_carry.callback(
        source_sprint_id, item_id, target_sprint_id, ticket_ids, as_json
    )


_PUBLIC_COMMAND_HELP = {
    ("send-message",): "Send one message to one existing Panels conversation.",
    ("project", "list"): "List Projects as bounded summaries.",
    ("project", "show"): "Read one Project record or selected parts.",
    ("project", "create"): "Create one Project.",
    ("project", "set"): "Change one Project field.",
    ("day", "show"): "Read one Day record or selected parts.",
    ("day", "set"): "Change one authored Day field.",
    ("day", "add-ticket"): "Add one Ticket to one Day.",
    ("day", "remove-ticket"): "Remove one Ticket from one Day.",
    ("ticket", "create"): "Create one Ticket from one JSON object.",
    ("ticket", "list"): "List Tickets with bounded filters.",
    ("ticket", "show"): "Read the current Worker's Ticket or one named Ticket.",
    ("ticket", "edit"): "Apply one or more Ticket field edits from one JSON object.",
    ("ticket", "delete"): "Permanently delete one Ticket after explicit confirmation.",
    ("ticket", "proposal"): "Submit, accept, or revise the current Ticket proposal.",
    ("ticket", "complete"): "Complete one user-owned Stage directly.",
    ("ticket", "history"): "Read the current Worker conversation for one Ticket.",
    (
        "ticket",
        "restart-worker",
    ): "Restart one dead Worker through the existing recovery route.",
    ("ticket", "block"): "Add one blocking Ticket relationship.",
    ("ticket", "unblock"): "Remove one blocking Ticket relationship.",
    (
        "ticket",
        "request-help",
    ): "Record and send one Ticket help request from standard input.",
    ("sprint", "create"): "Create one Sprint.",
    ("sprint", "list"): "List Sprints as bounded summaries.",
    ("sprint", "show"): "Read one Sprint record or selected parts.",
    ("sprint", "set"): "Change one Sprint field.",
    ("sprint", "add-item"): "Add one Sprint Item to one Sprint.",
    ("sprint", "remove-item"): "Remove one Sprint Item from one Sprint.",
    ("sprint", "list-items"): "List the Sprint Items tracked in one Sprint.",
    (
        "sprint",
        "carry-item",
    ): "Carry one Sprint Item and selected Tickets to another Sprint.",
    ("sprint", "item", "create"): "Create one Sprint Item.",
    ("sprint", "item", "list"): "List Sprint Items as bounded summaries.",
    ("sprint", "item", "show"): "Read one Sprint Item record or selected parts.",
    ("sprint", "item", "set"): "Change one Sprint Item field.",
    ("sprint", "item", "delete"): "Permanently delete one empty Sprint Item.",
    ("sprint", "item", "add-ticket"): "Classify one Ticket under one Sprint Item.",
    (
        "sprint",
        "item",
        "remove-ticket",
    ): "Remove one Ticket's Sprint Item classification.",
    (
        "sprint",
        "item",
        "workspace",
    ): "Read one Sprint Item, its artifacts, and child Tickets.",
    ("sprint", "item", "artifact", "list"): "List one Sprint Item's artifacts.",
    ("sprint", "item", "artifact", "write"): "Write one Sprint Item artifact.",
    ("sprint", "item", "artifact", "delete"): "Delete one Sprint Item artifact.",
}


def _set_public_command_help() -> None:
    for path, help_text in _PUBLIC_COMMAND_HELP.items():
        command: click.Command = main
        for name in path:
            assert isinstance(command, click.Group)
            command = command.commands[name]
        command.help = help_text


_set_public_command_help()


if __name__ == "__main__":
    main()
