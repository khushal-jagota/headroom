"""The `panels` CLI.

The command tree mirrors the product model:

* send-message: send text to one Panels conversation owner
* ticket: create, inspect, organize, and approve tickets
* manager: create, inspect, edit, and populate ordinary managers

Administrative and compatibility commands remain callable, but ordinary help exposes
only the five actor-neutral roots used for Panels work.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import click

from planner.attention.cli import attention as attention_group
from planner.cli import http
from planner.cli.artifact_io import read_artifact_source
from planner.cli.errors import PanelsGroup
from planner.cli.record_projection import (
    RecordPart,
    parse_part_names,
    part,
    project_record,
    render_text,
)
from planner.credentials.cli import credential as credential_group
from planner.datasets.cli import dataset as dataset_group
from planner.environments.cli import environment as environment_group
from planner.executor.cli import executor_group
from planner.goals.cli import goal as goal_group
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.measure_sources.cli import measure_source as measure_source_group
from planner.measures.cli import measure as measure_group
from planner.message_delivery.contracts import MessageDeliveryMode
from planner.previews.cli import preview as preview_group
from planner.server_lifecycle.cli import serve as serve_command
from planner.tickets.contracts import TicketLocale
from planner.worker_settings.contracts import SkillPatch
from planner.worker_types.contracts import BRIEF_FIELD_ID

_LOCALES = [locale.value for locale in TicketLocale]
_TICKET_ID_ENV = "PLAN_TICKET_ID"
_ACTOR_ENV = "PLAN_ACTOR"
_MANAGER_ID_ENV = "PLAN_MANAGER_ID"


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
    owner, and a Manager keeps it as itself. A Worker cannot hold a review.
    """
    actor = os.environ.get(_ACTOR_ENV, "").strip()
    ticket_id = os.environ.get(_TICKET_ID_ENV, "").strip()
    manager_id = os.environ.get(_MANAGER_ID_ENV, "").strip()
    if actor == "goal" and os.environ.get("PLAN_GOAL_ID", "").strip():
        return {"kind": "goal", "id": os.environ["PLAN_GOAL_ID"].strip()}
    if actor == "chief":
        return {"kind": "chief", "id": "chief"}
    if actor == "worker" and ticket_id:
        return {"kind": "ticket", "id": ticket_id}
    if actor == "manager" and manager_id:
        return {"kind": "manager", "id": manager_id}
    return {"kind": "owner", "id": "owner"}


def resolve_holder(raw: str, as_json: bool) -> dict[str, str]:
    """Turn a holder word or id into the principal object the API takes."""
    holder = raw.strip()
    alias = _HOLDER_ALIASES.get(holder.lower())
    if alias is not None:
        return {"kind": alias[0], "id": alias[1]}
    if holder.startswith("si_"):
        return {"kind": "manager", "id": holder}
    http.fail_validation(
        "holder must be me, chief, or a Manager id",
        as_json,
    )
    raise AssertionError("unreachable")


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
    "goal": "goal",
    "recap": "recap",
    "project": "project",
    "project-id": "project_id",
}

_PROJECT_FIELDS = {
    "folder-path": "folder_path",
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


def refuse_worker_body_file(body_file: str | None, as_json: bool, stdin_form: str) -> None:
    if body_file is not None:
        http.fail_validation(f"--body-file is no longer supported: {stdin_form}", as_json)


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
        requested = () if raw_part_names is None else tuple(raw_part_names.split(","))
        if "rejection" in requested:
            http.fail_validation(
                str(exc),
                as_json,
                no_route=(
                    "Revision feedback arrives in the Worker prompt. No panels command reads it."
                ),
            )
        http.fail_validation(str(exc), as_json)
    http.emit(projection, as_json, render_text(projection))


def _ticket_record(
    data: dict[str, Any],
    raw_part_names: str | None = None,
    *,
    as_json: bool = True,
) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    header_keys = (
        "id",
        "title",
        "worker_type",
        "worker",
        "worker_skill_path",
        "locale",
        "employee_backend",
        "employee_launch_model",
        "employee_launch_reasoning_effort",
        "stage",
        "ticket_status",
        "is_backlogged",
        "record_sha256",
        "deadline",
        "project_id",
        "manager_id",
        "ceiling",
        "ceiling_holder",
    )
    header = {key: data[key] for key in header_keys if key in data}
    manifest = data["workflow"]
    parts = {
        field["id"]: part(
            data["field_values"].get(field["id"]),
            approval=data.get("field_approvals", {}).get(field["id"]),
        )
        for field in manifest["fields"]
    }
    parts["workflow"] = part(data.get("workflow"))
    parts["proposal"] = part(data.get("pending_proposal"))
    parts["recap"] = part(data.get("recap", ""))
    # Current manifests deliberately omit retired fields. An explicit old-field
    # request uses the same retained-body read as the selected workspace, without
    # expanding every historical body or changing the current write declaration.
    for field in dict.fromkeys(_record_parts(raw_part_names, as_json) or ()):
        if field in parts or field == "rejection":
            continue
        body = http.send(
            "GET",
            f"/api/tickets/{quote(str(data['id']), safe='')}/workspace/fields/"
            f"{quote(field, safe='')}",
            as_json=as_json,
        )
        parts[field] = part(body["value"])
    return header, parts


def _manager_record(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    header_keys = (
        "id",
        "title",
        "project_id",
        "project",
    )
    header = {key: data[key] for key in header_keys if key in data}
    header["settled"] = data["settled"]
    # The Manager's agent is part of the Manager record. It used to be a read of its
    # own, which returned these four values and nothing this read did not already carry.
    manager_data = data.get("manager")
    if manager_data is not None:
        launch = manager_data["launch_configuration"]
        header |= {
            "manager_agent_key": manager_data["agent_key"],
            "manager_conversation_id": manager_data["conversation_id"],
            "employee_backend": launch["employee_backend"],
            "employee_launch_model": launch["employee_launch_model"],
            "employee_launch_reasoning_effort": launch["employee_launch_reasoning_effort"],
        }
    return (header, {"goal": part(data["goal"]), "recap": part(data["recap"])})


def _project_record(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, RecordPart]]:
    return (
        {key: data[key] for key in ("id", "name", "folder_path") if key in data},
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


def _format_tickets(tickets: list[dict[str, Any]]) -> str:
    return _lines(
        tickets,
        lambda t: f"{t['id']} {t['stage']} {t['title']}",
    )


def _format_ticket_summaries(tickets: list[dict[str, Any]]) -> str:
    return _lines(
        tickets,
        lambda ticket: (
            f"{ticket['id']} {ticket['stage']} {ticket['ticket_status']} "
            f"{ticket['title']}"
            + (" | backlogged" if ticket.get("is_backlogged") else "")
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
    func = click.option("--offset", type=click.IntRange(min=0), default=0, show_default=True)(func)
    return click.option(
        "--limit",
        type=click.IntRange(min=1),
        default=DEFAULT_LIST_LIMIT,
        show_default=True,
    )(func)


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
        help="Print machine-readable JSON. Errors are printed as JSON on stderr.",
    )(func)


@click.group(cls=PanelsGroup)
def main() -> None:
    """Operate the local planner server."""


main.add_command(executor_group)
main.add_command(environment_group)
main.add_command(credential_group)
main.add_command(goal_group)
main.add_command(measure_group)
main.add_command(dataset_group)
main.add_command(attention_group)
main.add_command(measure_source_group)
main.add_command(preview_group)
environment_group.hidden = True


@main.command("send-message")
@click.option("--owner", is_flag=True, help="Send to Khushal.")
@click.option("--chief", is_flag=True, help="Send to the Chief of Staff.")
@click.option("--goal", "goal_id", default=None, help="Send to a goal agent.")
@click.option("--ticket", "ticket_id", default=None, help="Send to a Ticket worker.")
@click.option(
    "--manager",
    "manager_id",
    default=None,
    help="Send to a Manager.",
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
@click.option("--body-file", default=None, help="Read message text from this file, or -.")
@json_option
def send_message(
    owner: bool,
    chief: bool,
    ticket_id: str | None,
    manager_id: str | None,
    goal_id: str | None,
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
            manager_id is not None,
            goal_id is not None,
        )
        if selected
    )
    if targets != 1:
        http.fail_validation(
            "send-message requires exactly one of --owner, --chief, --ticket, --manager, or --goal",
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
    elif goal_id is not None:
        recipient = {"kind": "goal", "id": goal_id}
    else:
        assert manager_id is not None
        recipient = {"kind": "manager", "id": manager_id}
    data = http.send(
        "POST",
        "/api/messages/send",
        as_json=as_json,
        json_body={"target": recipient, "message": text, "mode": mode},
    )
    http.emit(data, as_json, f"message {data['fate']}")


main.add_command(serve_command)


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
    config = load_config(os.environ.get("PLAN_CONFIG_PATH", str(launch_root / "config.yaml")))
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


# --- worker types -------------------------------------------------------------


@main.group("worker-type", hidden=True)
def worker_type_group() -> None:
    """Inspect and declare the Worker types."""


@worker_type_group.command("list")
@json_option
def worker_type_list(as_json: bool) -> None:
    data = http.send("GET", "/api/worker-types?supported=true", as_json=as_json)
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
    lines = [f"{data['worker_type']} saved"]
    for migration in data.get("migrations", []):
        lines.append(
            f"{migration['title']}: Stage {migration['old_stage']} -> {migration['new_stage']}; "
            f"ceiling {migration['old_ceiling']} -> {migration['new_ceiling']}"
        )
    http.emit(data, as_json, "\n".join(lines))


@worker_type_group.command("skill")
@click.argument("worker_type")
@click.option("--description", help="The one-line description in the frontmatter.")
@json_option
def worker_type_skill(worker_type: str, description: str | None, as_json: bool) -> None:
    """Edit this Worker type's skill. Optional Markdown body on redirected stdin."""
    patch = _read_skill_patch(description, as_json)
    worker_path = quote(worker_type, safe="")
    definition = http.send("GET", f"/api/worker-types/{worker_path}", as_json=as_json)
    skill_name = definition["profile"]["specialist_skill"]
    data = http.send(
        "PATCH",
        f"/api/skills/{quote(skill_name, safe='')}",
        json_body=patch,
        as_json=as_json,
    )
    if as_json:
        # Keep the legacy settings envelope while the write uses the common door.
        detail = http.send("GET", f"/api/workers/{worker_path}", as_json=True)
        data = detail["settings"]
    http.emit(data, as_json, f"{skill_name} saved")


# --- project ------------------------------------------------------------------


@main.group("skill", hidden=True)
def skill_group() -> None:
    """Inspect and edit the managed skills."""


def _read_skill_patch(description: str | None, as_json: bool) -> SkillPatch:
    patch: SkillPatch = {}
    if description is not None:
        if not description.strip():
            http.fail_validation("description must not be blank", as_json)
        patch["description"] = description
    body = "" if sys.stdin.isatty() else sys.stdin.read()
    if body:
        if not body.strip():
            http.fail_validation("markdown_body must not be blank", as_json)
        patch["markdown_body"] = body
    if not patch:
        http.fail_validation("supply --description or a skill body on redirected stdin", as_json)
    return patch


@skill_group.command("list")
@json_option
def skill_list(as_json: bool) -> None:
    """List the managed skill names."""
    data = http.send("GET", "/api/skills", as_json=as_json)
    http.emit(data, as_json, _lines(data["skills"], lambda item: str(item["name"])))


@skill_group.command("show")
@click.argument("name")
@json_option
def skill_show(name: str, as_json: bool) -> None:
    """Print a skill's Markdown body, or its full record as JSON."""
    data = http.send("GET", f"/api/skills/{quote(name, safe='')}", as_json=as_json)
    if as_json:
        http.emit(data, True, "")
    click.echo(data["markdown_body"], nl=False)


@skill_group.command("edit")
@click.argument("name")
@click.option("--description", help="The one-line description in the frontmatter.")
@json_option
def skill_edit(name: str, description: str | None, as_json: bool) -> None:
    """Edit a skill. Optional Markdown body on redirected stdin."""
    patch = _read_skill_patch(description, as_json)
    data = http.send(
        "PATCH",
        f"/api/skills/{quote(name, safe='')}",
        json_body=patch,
        as_json=as_json,
    )
    http.emit(data, as_json, f"{data['name']} saved")


@skill_group.command("apply")
@click.option(
    "--input-json", "input_json", default="-", help="Exact manifest file, or - for stdin."
)
@json_option
def skill_apply(input_json: str, as_json: bool) -> None:
    """Atomically apply a reviewed skill manifest."""
    try:
        manifest = json.loads(_read_source(input_json, as_json))
    except ValueError:
        http.fail_validation("manifest must contain a JSON object", as_json)
    if not isinstance(manifest, dict):
        http.fail_validation("manifest must contain a JSON object", as_json)
    data = http.send("POST", "/api/skills/apply", as_json=as_json, json_body=manifest)
    http.emit(data, as_json, f"applied {len(data['skills'])} skills")


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
            f"{item['skill_name']}: {item['kind']} {item['reference']}" for item in references
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
@click.option("--summary", default=None, help="Optional project summary text.")
@click.option("--folder-path", default=None, help="Optional absolute Project folder path.")
@json_option
def project_create(
    name: str,
    summary: str | None,
    folder_path: str | None,
    as_json: bool,
) -> None:
    body: dict[str, Any] = {"name": name}
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
    if field in {"name"} and new_value is None:
        http.fail_validation(f"{field} cannot be cleared", as_json)
    data = http.send(
        "PATCH",
        f"/api/projects/{project_id}",
        as_json=as_json,
        json_body={
            api_field: (
                None
                if field == "folder-path" and new_value is None
                else ""
                if new_value is None
                else new_value
            )
        },
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


# --- ticket -------------------------------------------------------------------


@main.group("ticket")
def ticket() -> None:
    """Create, inspect, organize, and approve tickets."""


@ticket.command("create")
@click.option(
    "--input-json",
    required=True,
    help="Read one Ticket create object from this file, or - for standard input.",
)
@click.option("--locale", type=click.Choice(_LOCALES), default=None)
@json_option
def ticket_create(input_json: str, locale: str | None, as_json: bool) -> None:
    """Create one Ticket from one JSON object through the existing atomic writer."""
    body = _read_json_object(input_json, as_json, "Ticket create input")
    if locale is not None:
        if "locale" in body:
            http.fail_validation("locale accepts one input source", as_json)
        body["locale"] = locale
    data = http.send("POST", "/api/tickets", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['stage']}\n{data['supervision_receipt']}")


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
    header, parts = _ticket_record(data, part_names, as_json=as_json)
    _emit_record(header, parts, part_names, as_json)


@ticket.command("backlog")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def ticket_backlog(ticket_id: str | None, as_json: bool) -> None:
    """Defer automatic worker steps while preserving the Ticket's work."""
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/backlog",
        as_json=as_json,
        json_body={},
    )
    http.emit(data, as_json, "Ticket backlogged.")


@ticket.command("resume")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def ticket_resume(ticket_id: str | None, as_json: bool) -> None:
    """Resume automatic steps under the Ticket's existing gates."""
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/resume",
        as_json=as_json,
        json_body={},
    )
    http.emit(data, as_json, "Ticket resumed.")


@ticket.command("delete")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Delete even a ticket that still looks running, for a stuck ticket status.",
)
@json_option
def ticket_delete(ticket_id: str | None, force: bool, as_json: bool) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "DELETE",
        f"/api/tickets/{tid}",
        as_json=as_json,
        params={"force": True} if force else None,
    )
    http.emit(data, as_json, f"{tid} permanently deleted")


@ticket.command("list")
@click.option(
    "--backlogged/--not-backlogged",
    "is_backlogged",
    default=None,
    help="Filter explicit Ticket deferral.",
)
@click.option("--worker-type", multiple=True, help="Filter Worker type. Repeat for more.")
@click.option("--stage", multiple=True, help="Include this Stage. Repeat for more.")
@click.option("--exclude-stage", multiple=True, help="Exclude this Stage. Repeat for more.")
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
@click.option(
    "--manager",
    "manager",
    default=None,
    help="Only show tickets in this item.",
)
@bounded_options
@json_option
def ticket_list(
    is_backlogged: bool | None,
    worker_type: tuple[str, ...],
    stage: tuple[str, ...],
    exclude_stage: tuple[str, ...],
    ticket_status: tuple[str, ...],
    exclude_ticket_status: tuple[str, ...],
    include_terminal: bool,
    search: str | None,
    project: str | None,
    project_id: str | None,
    manager: str | None,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    params = _drop_none(
        {
            "is_backlogged": is_backlogged,
            "worker_type": list(worker_type) or None,
            "stage": list(stage) or None,
            "exclude_stage": list(exclude_stage) or None,
            "ticket_status": list(ticket_status) or None,
            "exclude_ticket_status": list(exclude_ticket_status) or None,
            "include_terminal": include_terminal or None,
            "search": search,
            "project": project,
            "project_id": project_id,
            "manager_id": manager,
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
        http.fail_validation("Ticket edit input must contain at least one field", as_json)
    data = http.send("PATCH", f"/api/tickets/{tid}", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} edited")


@ticket.command("complete")
@click.argument("ticket_id")
@click.argument("field")
@click.option("--value", default=None, help="Complete the gate with this value.")
@click.option("--body-file", default=None, help="Read the value from this file, or - for stdin.")
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


@ticket.command("employee-configuration", hidden=True)
@click.argument("ticket_id")
@click.option("--backend", required=True, help="Registered employee backend.")
@click.option("--model", required=True, help="Model that backend runs this Ticket's worker on.")
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


def _approve_ticket_proposal(
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
    manifest = detail["workflow"]
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
            http.fail_validation("--kickoff-title only applies while approving the brief", as_json)
        http.send(
            "PATCH",
            f"/api/tickets/{tid}",
            as_json=as_json,
            json_body={"title": kickoff_title},
        )
    field_payload: dict[str, Any] = {
        "next_ceiling": ceiling,
        "next_holder": (
            resolve_holder(holder, as_json) if holder is not None else caller_principal()
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
            http.fail_validation("approval accepts only one edited body option", as_json)
        field_payload["edited_body"] = _read_source(kickoff_note_file, as_json)
    data = http.send(
        "POST",
        f"/api/tickets/{tid}/accept/{field}",
        as_json=as_json,
        json_body=field_payload,
    )
    http.emit(data, as_json, f"{data['id']} approved {field}")


def _revise_ticket_proposal(
    ticket_id: str | None, message: str | None, body_file: str | None, as_json: bool
) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    if (message is None) == (body_file is None):
        http.fail_validation("reject requires exactly one of --message or --body-file", as_json)
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
@click.option("--before", type=int, default=None, help="Read the page before this sequence.")
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
        http.fail_validation("history reads one direction: --after or --before", as_json)
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
@click.option("--backend", default=None, help="Registered employee backend to restart on.")
@click.option("--model", default=None, help="Model that backend runs this Ticket's worker on.")
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
    launch = f"{configuration['employee_backend']} {configuration['employee_launch_model']}"
    if data["started"]:
        human_result = f"{ticket_id} restarted on {launch}"
    elif data["delivery_fate"] == "uncertain":
        human_result = f"{ticket_id} start delivery is uncertain. The Worker claim remains held."
    elif data["delivery_fate"] == "refused":
        human_result = f"{ticket_id} start delivery was refused"
    else:
        human_result = f"{ticket_id} did not start: {data['not_started_because']}"
    http.emit(
        data,
        as_json,
        human_result,
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


# --- managers -----------------------------------------------------------------


@main.group("manager")
def manager() -> None:
    """Manage ordinary Managers. Settled Managers are finished destinations.

    Choose an active Manager for new work. Reads expose the boolean settled key.
    An absent settled field does not establish that a Manager is active.
    """


@manager.command("create")
@click.option("--title", required=True, help="Manager title.")
@click.option("--project", default=None, help="Project name.")
@click.option("--project-id", default=None, help="Project id.")
@click.option("--goal-file", default=None, help="Optional goal file, or - for stdin.")
@json_option
def manager_create(
    title: str,
    project: str | None,
    project_id: str | None,
    goal_file: str | None,
    as_json: bool,
) -> None:
    body: dict[str, Any] = {
        "title": title,
        "goal": read_optional_body(goal_file, as_json) or "",
    }
    add_project_selectors(
        body, project=project, project_id=project_id, required=True, as_json=as_json
    )
    data = http.send("POST", "/api/managers", as_json=as_json, json_body=body)
    item_id = data["id"]
    startup = data.get("startup")
    human_result = f"Created manager {item_id}."
    if startup is None:
        human_result += " Idle (no goal)."
    else:
        fate = startup["fate"]
        human_result += f" Startup {fate}."
        if fate == "queued" and startup.get("queue_position") is not None:
            human_result += f" Queue position: {startup['queue_position']}."
        if fate in {"refused", "uncertain", "failed"}:
            reason = startup.get("refusal_reason") or startup.get("error")
            if reason:
                human_result += f" {reason}"
            human_result += f"\nThe manager is saved. Inspect: panels manager show {item_id}"
            if fate in {"uncertain", "failed"}:
                human_result += (
                    "\nCheck its conversation before any resend. Do not create it again."
                )
            else:
                human_result += (
                    f"\nRetry on this manager: panels send-message --manager {item_id}"
                    ' --message "<goal>"'
                )
    http.emit(data, as_json, human_result)


@manager.command("list")
@click.option("--search", default=None, help="Find a Manager by title.")
@click.option("--active", is_flag=True, help="Only show active Managers (settled=false).")
@click.option("--settled", is_flag=True, help="Only show settled Managers (finished destinations).")
@click.option("--project", default=None, help="Only show project name.")
@click.option("--project-id", default=None, help="Only show project id.")
@bounded_options
@json_option
def manager_list(
    search: str | None,
    active: bool,
    settled: bool,
    project: str | None,
    project_id: str | None,
    limit: int,
    offset: int,
    as_json: bool,
) -> None:
    if active and settled:
        http.fail_validation("use only one of --active or --settled", as_json)
    params = _drop_none(
        {
            "settled": False if active else True if settled else None,
            "search": search,
            "project": project,
            "project_id": project_id,
            "limit": limit,
            "offset": offset,
        }
    )
    data = http.send(
        "GET",
        "/api/managers",
        as_json=as_json,
        params={"detail": "summary", **params},
    )
    http.emit(
        data,
        as_json,
        _format_bounded(
            _lines(
                data["managers"],
                lambda i: f"{i['id']} {i['title']} [{'settled' if i['settled'] else 'active'}]",
            ),
            data["page"],
        ),
    )


@manager.command("show")
@click.argument("item_id")
@click.argument("part_names", required=False)
@json_option
def manager_show(item_id: str, part_names: str | None, as_json: bool) -> None:
    """Show an Item manifest or selected comma-separated PART_NAMES."""
    data = http.send(
        "GET",
        "/api/managers",
        as_json=as_json,
        params={"detail": "full", "id": item_id},
    )
    header, parts = _manager_record(data)
    _emit_record(header, parts, part_names, as_json)


@manager.command("delete")
@click.argument("item_id")
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Permanently delete the manager and its history.",
)
@json_option
def manager_delete(item_id: str, yes: bool, as_json: bool) -> None:
    if not yes:
        http.fail_validation("permanent deletion requires --yes", as_json)
    data = http.send("DELETE", f"/api/managers/{item_id}", as_json=as_json)
    http.emit(data, as_json, f"{item_id} permanently deleted")


@manager.command("set")
@click.argument("item_id")
@click.argument("field", type=click.Choice(sorted(_ITEM_FIELDS)))
@click.option("--value", default=None, help="Set the field to this value.")
@click.option("--body-file", default=None, help="Read field text from this file, or - for stdin.")
@click.option("--clear", is_flag=True, default=False, help="Clear nullable fields.")
@json_option
def manager_set(
    item_id: str,
    field: str,
    value: str | None,
    body_file: str | None,
    clear: bool,
    as_json: bool,
) -> None:
    api_field = _ITEM_FIELDS[field]
    new_value = read_value_or_file(value, body_file, clear, as_json, field)
    if field in {"title", "project", "project-id"} and new_value is None:
        http.fail_validation(f"{field} cannot be cleared", as_json)
    data = http.send(
        "PATCH",
        f"/api/managers/{item_id}",
        as_json=as_json,
        json_body={api_field: new_value},
    )
    http.emit(data, as_json, f"{data['id']} {field} set")


@manager.command("add-ticket")
@click.argument("item_id")
@click.argument("ticket_id")
@json_option
def manager_add_ticket(item_id: str, ticket_id: str, as_json: bool) -> None:
    data = http.send(
        "PUT",
        f"/api/collections/manager_tickets/{item_id}/{ticket_id}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{ticket_id} added to {item_id}")


@manager.command("remove-ticket")
@click.argument("item_id")
@click.argument("ticket_id")
@json_option
def manager_remove_ticket(item_id: str, ticket_id: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        f"/api/collections/manager_tickets/{item_id}/{ticket_id}",
        as_json=as_json,
    )
    http.emit(data, as_json, f"{ticket_id} removed from {item_id}")


@manager.command("workspace")
@click.argument("item_id")
@json_option
def manager_workspace(item_id: str, as_json: bool) -> None:
    """Read this Manager, its artifacts, and its current child Tickets."""
    data = http.send("GET", f"/api/managers/{item_id}/workspace", as_json=as_json)
    http.emit(data, as_json, f"{item_id} {len(data['tickets'])} current Tickets")


# The Manager's artifact subdirectory. These commands count paths from it, while the
# route counts from the Manager root, so the prefix is added here.
_ARTIFACTS_DIRECTORY = "artifacts"


@manager.group("artifact")
def manager_artifact() -> None:
    """Read and write a Manager's own artifacts."""


@manager_artifact.command("list")
@click.argument("item_id")
@json_option
def manager_artifact_list(item_id: str, as_json: bool) -> None:
    data = http.send("GET", f"/api/managers/{item_id}/workspace", as_json=as_json)
    artifacts = sorted(_artifact_openable_paths(data["artifacts"]))
    http.emit(
        {"manager_id": item_id, "artifacts": artifacts},
        as_json,
        _lines(artifacts, str),
    )


def _artifact_openable_paths(entries: list[dict[str, Any]]) -> list[str]:
    """Flatten the workspace's folded entries, which omit an index's supporting files."""
    prefix = f"{_ARTIFACTS_DIRECTORY}/"
    paths = []
    for entry in entries:
        opens = entry["opens"]
        if isinstance(opens, str) and opens.startswith(prefix):
            paths.append(opens[len(prefix) :])
        paths.extend(_artifact_openable_paths(entry["children"]))
    return paths


def _artifact_url(item_id: str, artifact_path: str) -> str:
    return f"/files/managers/{item_id}/{_ARTIFACTS_DIRECTORY}/{quote(artifact_path, safe='/')}"


@manager_artifact.command("read")
@click.argument("item_id")
@click.argument("artifact_path")
@json_option
def manager_artifact_read(item_id: str, artifact_path: str, as_json: bool) -> None:
    content = http.send_text(
        "GET",
        _artifact_url(item_id, artifact_path),
        as_json=as_json,
    )
    if as_json:
        http.emit(
            {"manager_id": item_id, "path": artifact_path, "content": content},
            as_json,
            content,
        )
    sys.stdout.write(content)


@manager_artifact.command("write")
@click.argument("item_id")
@click.argument("artifact_path")
@click.option("--body-file", required=True)
@json_option
def manager_artifact_write(item_id: str, artifact_path: str, body_file: str, as_json: bool) -> None:
    data = http.send(
        "PUT",
        _artifact_url(item_id, artifact_path),
        as_json=as_json,
        json_body={"content": read_artifact_source(body_file, as_json)},
    )
    http.emit(data, as_json, data["url"])


@manager_artifact.command("delete")
@click.argument("item_id")
@click.argument("artifact_path")
@json_option
def manager_artifact_delete(item_id: str, artifact_path: str, as_json: bool) -> None:
    data = http.send(
        "DELETE",
        _artifact_url(item_id, artifact_path),
        as_json=as_json,
    )
    http.emit(data, as_json, f"{artifact_path} deleted")


@manager.group("conversation", hidden=True)
def manager_conversation() -> None:
    """Compatibility and recovery paths for a Manager conversation."""


@manager_conversation.command("send")
@click.argument("item_id")
@click.option("--message", default=None, help="Message text.")
@click.option("--body-file", default=None, help="Read message text from this file, or -.")
@json_option
def manager_conversation_send(
    item_id: str, message: str | None, body_file: str | None, as_json: bool
) -> None:
    if (message is None) == (body_file is None):
        http.fail_validation("send requires exactly one of --message or --body-file", as_json)
    text = message if message is not None else _read_source(body_file or "", as_json)
    data = http.send(
        "POST",
        f"/api/managers/{item_id}/conversation/send",
        as_json=as_json,
        json_body={"content": [{"piece": "text", "text": text}], "sender_label": "You"},
    )
    http.emit(data, as_json, f"message {data['fate']}")


@manager_conversation.command("reset")
@click.argument("item_id")
@json_option
def manager_conversation_reset(item_id: str, as_json: bool) -> None:
    data = http.send("POST", f"/api/managers/{item_id}/conversation/reset", as_json=as_json)
    http.emit(data, as_json, f"{item_id} Manager conversation reset")


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
    header, parts = _ticket_record(data, part_names, as_json=as_json)
    native_skills = os.environ.get("PLAN_NATIVE_SKILLS_DIR")
    if native_skills and "worker" in data:
        header["worker_skill_path"] = str(Path(native_skills) / data["worker"] / "SKILL.md")
    _emit_record(header, parts, part_names, as_json)


def _submit_ticket_proposal(
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


@worker.command("propose")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Removed: pipe proposal text on stdin instead.")
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
    _submit_ticket_proposal(ticket_id, body_file, recap, recap_file, as_json)


def _request_ticket_help(
    ticket_id: str | None,
    owner: bool,
    chief: bool,
    recipient_ticket_id: str | None,
    recipient_manager_id: str | None,
    as_json: bool,
) -> None:
    tid = resolve_ticket_id(ticket_id, as_json)
    selected = sum(
        int(value)
        for value in (
            owner,
            chief,
            recipient_ticket_id is not None,
            recipient_manager_id is not None,
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
    elif recipient_manager_id is not None:
        recipient = {"kind": "manager", "id": recipient_manager_id}
    message = read_worker_stdin_body(as_json, "help message")
    body: dict[str, Any] = {"message": message}
    if recipient is not None:
        body["recipient"] = recipient
    data = http.send("POST", f"/api/tickets/{tid}/request-help", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"help message {data['fate']}")


@worker.command("request-help")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--owner", is_flag=True, help="Ask Khushal for help.")
@click.option("--chief", is_flag=True, help="Ask the Chief of Staff for help.")
@click.option("--ticket", "recipient_ticket_id", default=None, help="Ask another Ticket worker.")
@click.option(
    "--manager",
    "recipient_manager_id",
    default=None,
    help="Ask a Manager.",
)
@json_option
def worker_request_help(
    ticket_id: str | None,
    owner: bool,
    chief: bool,
    recipient_ticket_id: str | None,
    recipient_manager_id: str | None,
    as_json: bool,
) -> None:
    _request_ticket_help(
        ticket_id,
        owner,
        chief,
        recipient_ticket_id,
        recipient_manager_id,
        as_json,
    )


@worker.command("recap")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Removed: pipe recap text on stdin instead.")
@json_option
def worker_recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    refuse_worker_body_file(body_file, as_json, "pipe recap text on stdin instead")
    body = read_worker_stdin_body(as_json, "recap")
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("PATCH", f"/api/tickets/{tid}", as_json=as_json, json_body={"recap": body})
    http.emit(data, as_json, f"recap written on {data['id']}")


@ticket.command("proposal")
@click.argument("proposal_args", nargs=-1, metavar="[TICKET_ID] ACTION")
@click.option("--ceiling", default=None, help="Next ceiling for an accepted proposal.")
@click.option("--holder", default=None, help="Next ceiling holder for an accepted proposal.")
@click.option("--edit-file", default=None, help="Edited accepted body, or - for stdin.")
@click.option("--kickoff-title", default=None, help="Edited Kickoff title.")
@click.option("--kickoff-note-file", default=None, help="Edited Kickoff note, or - for stdin.")
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
        http.fail_validation(f"{action} does not accept proposal approval options", as_json)
    if action == "submit":
        _submit_ticket_proposal(ticket_id, None, None, None, as_json)
        return
    if action == "revise":
        _revise_ticket_proposal(ticket_id, None, "-", as_json)
        return
    _approve_ticket_proposal(
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
@click.option("--ticket", "recipient_ticket_id", default=None, help="Ask another Worker.")
@click.option(
    "--manager",
    "recipient_manager_id",
    default=None,
    help="Ask a Manager.",
)
@json_option
def ticket_request_help(
    ticket_id: str | None,
    owner: bool,
    chief: bool,
    recipient_ticket_id: str | None,
    recipient_manager_id: str | None,
    as_json: bool,
) -> None:
    """Record that this Ticket needs help and send the stdin message to one recipient."""
    _request_ticket_help(
        ticket_id,
        owner,
        chief,
        recipient_ticket_id,
        recipient_manager_id,
        as_json,
    )


_PUBLIC_COMMAND_HELP = {
    ("send-message",): "Send one message to one existing Panels conversation.",
    ("project", "list"): "List Projects as bounded summaries.",
    ("project", "show"): "Read one Project record or selected parts.",
    ("project", "create"): "Create one Project.",
    ("project", "set"): "Change one Project field.",
    ("ticket", "create"): (
        "Create one Ticket from one JSON object. Choose an active Manager for new work. "
        "Settled Managers are finished destinations. Read the settled boolean first. "
        "An absent settled field does not establish active state."
    ),
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
    ("manager", "create"): "Create one Manager.",
    ("manager", "list"): (
        "List Managers as bounded summaries with the settled boolean. "
        "Without --active or --settled, include both states. Settled Managers are finished "
        "destinations. Choose an active Manager for new work. "
        "An absent settled field does not establish active state."
    ),
    ("manager", "show"): (
        "Read one Manager record or selected parts, including the settled boolean. "
        "Settled Managers are finished destinations. Choose an active Manager "
        "for new work. An absent settled field does not establish active state."
    ),
    ("manager", "set"): "Change one Manager field.",
    ("manager", "delete"): "Permanently delete one empty Manager.",
    ("manager", "add-ticket"): "Classify one Ticket under one Manager.",
    (
        "manager",
        "remove-ticket",
    ): "Remove one Ticket's Manager classification.",
    (
        "manager",
        "workspace",
    ): "Read one Manager, its artifacts, and child Tickets.",
    ("manager", "artifact", "list"): (
        "List openable artifact paths from the workspace. Index folders hide supporting files."
    ),
    ("manager", "artifact", "read"): "Read one Manager artifact as exact text.",
    ("manager", "artifact", "write"): "Write one Manager artifact.",
    ("manager", "artifact", "delete"): "Delete one Manager artifact.",
}


def _set_public_command_help() -> None:
    for path, help_text in _PUBLIC_COMMAND_HELP.items():
        command: click.Command = main
        for name in path:
            assert isinstance(command, click.Group)
            child = command.commands.get(name)
            if child is None:
                break
            command = child
        else:
            command.help = help_text


@ticket.command("assign")
@click.argument("ticket_id")
@click.option("--chief", is_flag=True, default=False, help="Assign to Chief.")
@click.option(
    "--manager",
    "item_id",
    default=None,
    help="Ordinary manager id.",
)
@json_option
def ticket_assign(ticket_id: str, chief: bool, item_id: str | None, as_json: bool) -> None:
    """Assign one Worker to an explicit Manager.

    Choose an active Manager for new work. Settled Managers are finished
    destinations. Read the settled boolean first. An absent settled field does not
    establish active state.
    """
    if chief == (item_id is not None):
        http.fail_validation("use exactly one of --chief or --manager", as_json)
    parent = {"kind": "chief", "id": "chief"} if chief else {"kind": "manager", "id": item_id}
    data = http.send(
        "PATCH",
        f"/api/tickets/{ticket_id}",
        as_json=as_json,
        json_body={"manager": parent},
    )
    http.emit(data, as_json, f"{ticket_id} assigned")


@manager.command("settle")
@click.argument("item_id")
@click.option("--active", is_flag=True, default=False, help="Return to active instead.")
@json_option
def manager_settle(item_id: str, active: bool, as_json: bool) -> None:
    """Set the owner-controlled settled state of an ordinary manager."""
    data = http.send(
        "PATCH",
        f"/api/managers/{item_id}/settled",
        as_json=as_json,
        json_body={"settled": not active},
    )
    http.emit(data, as_json, f"{item_id} {'active' if active else 'settled'}")


_set_public_command_help()



if __name__ == "__main__":
    main()
