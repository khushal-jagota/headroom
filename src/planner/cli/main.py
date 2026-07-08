"""The `plan` CLI (§8). A single entry point speaking HTTP to the server. The CLI
is for agents and developer debugging; the human operates through the UI. No
resolution verbs exist here (accept/approve/scope are UI/API only).

Every verb supports --json (machine output; exit codes 0 success, 1
validation/domain error, 2 connection error). Long text arrives via
`--body-file <path>` where `-` reads stdin — body text is never an inline
argument. Every handler speaks HTTP through planner.cli.http, which owns all
output and exit-code decisions; a handler only builds a route + body."""

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

_PRIORITIES = ["P0", "P1", "P2", "P3"]
_FIELDS = ["success", "approach", "plan", "result"]
_LINK_KINDS = ["belongs_to", "parent_child", "blocks", "relates"]
_TICKET_ID_ENV = "PLAN_TICKET_ID"


# --- body input, id resolution, and render helpers (amendment 6) ---------------


def _read_source(spec: str, as_json: bool) -> str:
    """`-` -> stdin; else read the file. Unreadable file -> validation exit 1."""
    if spec == "-":
        return sys.stdin.read()
    try:
        return Path(spec).read_text(encoding="utf-8")
    except OSError:
        http.fail_validation(f"cannot read body file: {spec}", as_json)


def _positional_is_stdin_marker(positional: str | None) -> bool:
    """True only for a '-' the caller actually typed. click fills the positional from
    PLAN_TICKET_ID too, and an env value of '-' is a bogus ticket id, not a stdin
    request (amendment 6: body sources are explicit only)."""
    if positional != "-":
        return False
    source = click.get_current_context().get_parameter_source("ticket_id")
    return source == ParameterSource.COMMANDLINE


def read_body(positional_ticket_id: str | None, body_file: str | None, as_json: bool) -> str:
    """Required-body verbs. Body source is explicit only: --body-file PATH (or -), or a
    trailing positional that is literally '-' (the stdin marker, not a ticket id).
    Whitespace-only body -> validation exit 1. Returns the original unstripped text."""
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
    """Optional-body verbs. Body comes only from --body-file (which may be '-');
    absent flag -> None."""
    if body_file is None:
        return None
    return _read_source(body_file, as_json)


def resolve_ticket_id(positional: str | None, as_json: bool) -> str:
    """Effective ticket id. A real positional wins; a None or '-' positional falls back
    to PLAN_TICKET_ID (re-read here since click's envvar fallback does not fire when the
    positional is the '-' stdin marker). Missing -> validation exit 1."""
    if positional is not None and positional != "-":
        return positional
    env = os.environ.get(_TICKET_ID_ENV, "").strip()
    if env and env != "-":  # '-' is the stdin marker, never a ticket id
        return env
    http.fail_validation("ticket id required: pass it or set PLAN_TICKET_ID", as_json)


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _lines(rows: list[Any], fmt: Callable[[Any], str]) -> str:
    return "\n".join(fmt(r) for r in rows) if rows else "(none)"


def json_option(func: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--json",
        "as_json",
        is_flag=True,
        default=False,
        help="Emit machine-readable JSON (errors as JSON on stderr).",
    )(func)


@click.group()
def main() -> None:
    """plan — the planner CLI for agents and developer debugging."""


# --- serve (the one handler that actually works in T01) ---


@main.command("serve")
@json_option
def serve(as_json: bool) -> None:
    """Run the server (foreground)."""
    import os

    import uvicorn

    from planner.core.adapters.registry import build_adapters
    from planner.core.clock import build_clock
    from planner.core.config import HOST, load_config
    from planner.core.db import connect, create_schema
    from planner.core.server import create_app

    config = load_config()
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


# --- top-level proposal/recap/note verbs ---


@main.command("propose")
@click.argument("field", type=click.Choice(_FIELDS))
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
@json_option
def propose(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    """File or replace a proposal on a field (agent path)."""
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "POST", f"/api/tickets/{tid}/propose/{field}", as_json=as_json, json_body={"body": body}
    )
    http.emit(data, as_json, f"proposed {field} on {data['id']}")


@main.command("recap")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
@json_option
def recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    """Write a ticket recap (rejected before needs_approach)."""
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("PUT", f"/api/tickets/{tid}/recap", as_json=as_json, json_body={"body": body})
    http.emit(data, as_json, f"recap written on {data['id']}")


@main.command("note")
@click.argument("field", type=click.Choice(_FIELDS))
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
@json_option
def note(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    """Write a field's notes slot (human or agent, any time)."""
    body = read_body(ticket_id, body_file, as_json)
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send(
        "PUT", f"/api/tickets/{tid}/notes/{field}", as_json=as_json, json_body={"note": body}
    )
    http.emit(data, as_json, f"note {field} written on {data['id']}")


# --- ticket group ---


@main.group("ticket")
def ticket() -> None:
    """Ticket verbs."""


@ticket.command("create")
@click.option("--title", required=True, help="Ticket title (<= 200 chars).")
@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
@click.option("--deadline", default=None, help="ISO date.")
@click.option("--project", default=None, help="Vylo | Tribe | Learning | Other.")
@click.option("--sprint", default=None, help="Sprint id (standalone tickets only).")
@click.option("--sprint-item", "sprint_item", default=None, help="Sprint item id to parent under.")
@json_option
def ticket_create(
    title: str,
    priority: str | None,
    deadline: str | None,
    project: str | None,
    sprint: str | None,
    sprint_item: str | None,
    as_json: bool,
) -> None:
    """Create a ticket."""
    body: dict[str, Any] = {"title": title}
    if priority is not None:
        body["priority"] = priority
    if deadline is not None:
        body["deadline"] = deadline
    if project is not None:
        body["project"] = project
    if sprint is not None:
        body["sprint_id"] = sprint              # --sprint      -> sprint_id
    if sprint_item is not None:
        body["sprint_item_id"] = sprint_item    # --sprint-item -> sprint_item_id
    data = http.send("POST", "/api/tickets", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['state']}")


@ticket.command("show")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def ticket_show(ticket_id: str | None, as_json: bool) -> None:
    """Show a ticket (defaults to $PLAN_TICKET_ID)."""
    tid = resolve_ticket_id(ticket_id, as_json)
    data = http.send("GET", f"/api/tickets/{tid}", as_json=as_json)
    http.emit(data, as_json, f"{data['id']} {data['state']} {data['priority']} {data['title']}")


@ticket.command("list")
@click.option("--state", default=None)
@click.option("--project", default=None)
@click.option("--sprint", default=None)
@click.option("--day", default=None, help="Filter to a day's tickets: 'today' or an ISO date.")
@click.option("--date", default=None, help="ISO-date alias of --day.")
@json_option
def ticket_list(
    state: str | None,
    project: str | None,
    sprint: str | None,
    day: str | None,
    date: str | None,
    as_json: bool,
) -> None:
    """List tickets (optionally scoped to one day's board)."""
    params = _drop_none(
        {"state": state, "project": project, "sprint_id": sprint, "day": day or date}
    )
    data = http.send("GET", "/api/tickets", as_json=as_json, params=params)
    http.emit(
        data,
        as_json,
        _lines(data["tickets"], lambda t: f"{t['id']} {t['state']} {t['priority']} {t['title']}"),
    )


@ticket.command("set")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
@click.option("--deadline", default=None, help="ISO date, or 'none' to clear.")
@click.option("--day", default=None, help="ISO date or 'today' to assign to a day.")
@click.option("--sprint", default=None, help="Sprint id, or 'none' to clear.")
@json_option
def ticket_set(
    ticket_id: str | None,
    priority: str | None,
    deadline: str | None,
    day: str | None,
    sprint: str | None,
    as_json: bool,
) -> None:
    """Set priority / deadline / day / sprint (no ceiling or at_cap — scopes are human)."""
    tid = resolve_ticket_id(ticket_id, as_json)
    patch: dict[str, Any] = {}
    if priority is not None:
        patch["priority"] = priority
    if deadline is not None:
        patch["deadline"] = None if deadline == "none" else deadline
    if sprint is not None:
        patch["sprint_id"] = None if sprint == "none" else sprint
    if not patch and day is None:
        http.fail_validation("nothing to set: pass --priority/--deadline/--sprint/--day", as_json)
    data: Any = None
    if patch:
        data = http.send("PATCH", f"/api/tickets/{tid}", as_json=as_json, json_body=patch)
    if day is not None:
        data = http.send(
            "POST", f"/api/day/{day}/tickets", as_json=as_json, json_body={"ticket_id": tid}
        )
    # `data` is the LAST successful response: ticket_json if only PATCH, day view if --day.
    human = (
        f"day {data['id']}: {len(data['tickets'])} ticket(s)"
        if "tickets" in data
        else f"{data['id']} {data['state']}"
    )
    http.emit(data, as_json, human)


# --- worker group (identity) ---


@main.group("worker")
def worker() -> None:
    """Worker-agent verbs."""


@worker.command("my-ticket")
@json_option
def worker_my_ticket(as_json: bool) -> None:
    """The ticket you're working, resolved from your Hermes session."""
    key = os.environ.get("HERMES_SESSION_KEY", "").strip()
    if not key:
        http.fail_validation(
            "no HERMES_SESSION_KEY in env; not running as a ticket worker", as_json
        )
    data = http.send("GET", f"/api/tickets/by-session/{key}", as_json=as_json)
    http.emit(data, as_json, f"{data['id']} {data['state']} {data['priority']} {data['title']}")


# --- item group ---


@main.group("item")
def item() -> None:
    """Sprint-item verbs."""


@item.command("create")
@click.option("--title", required=True)
@click.option("--project", required=True, help="Vylo | Tribe | Learning | Other.")
@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
@click.option("--deadline", default=None, help="ISO date.")
@click.option("--sprint", default=None, help="Sprint id.")
@json_option
def item_create(
    title: str,
    project: str,
    priority: str | None,
    deadline: str | None,
    sprint: str | None,
    as_json: bool,
) -> None:
    """Create a sprint item."""
    body: dict[str, Any] = {"title": title, "project": project}
    if priority is not None:
        body["priority"] = priority
    if deadline is not None:
        body["deadline"] = deadline
    if sprint is not None:
        body["sprint_id"] = sprint
    data = http.send("POST", "/api/items", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['status']}")


@item.command("show")
@click.argument("item_id")
@json_option
def item_show(item_id: str, as_json: bool) -> None:
    """Show a sprint item."""
    data = http.send("GET", f"/api/items/{item_id}", as_json=as_json)
    http.emit(data, as_json, f"{data['id']} {data['status']} {data['priority']} {data['title']}")


@item.command("list")
@click.option("--status", default=None)
@click.option("--project", default=None)
@click.option("--backlog", is_flag=True, default=False, help="Items with no sprint (backlog).")
@json_option
def item_list(status: str | None, project: str | None, backlog: bool, as_json: bool) -> None:
    """List sprint items."""
    params = _drop_none({"status": status, "project": project})
    if backlog:
        params["sprint_id"] = "null"        # views.list_items maps "null" -> IS NULL
    data = http.send("GET", "/api/items", as_json=as_json, params=params)
    http.emit(
        data,
        as_json,
        _lines(data["items"], lambda i: f"{i['id']} {i['status']} {i['priority']} {i['title']}"),
    )


@item.command("set")
@click.argument("item_id")
@click.option("--status", type=click.Choice(["todo", "active", "blocked"]), default=None)
@click.option("--blocked-by", default=None, help="Comma-separated blocker ticket ids.")
@json_option
def item_set(item_id: str, status: str | None, blocked_by: str | None, as_json: bool) -> None:
    """Set an agent-permitted item transition (§3.2)."""
    body: dict[str, Any] = {}
    if status is not None:
        body["status"] = status
    if blocked_by is not None:
        body["blocked_by"] = [s for s in (x.strip() for x in blocked_by.split(",")) if s]
    if not body:
        http.fail_validation("nothing to set: pass --status/--blocked-by", as_json)
    data = http.send("PATCH", f"/api/items/{item_id}", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['status']}")


@item.command("propose-status")
@click.argument("item_id")
@click.option(
    "--to", "to_status", type=click.Choice(["done", "deferred_next_sprint"]), required=True
)
@click.option("--body-file", default=None, help="Optional rationale body, or - for stdin.")
@json_option
def item_propose_status(item_id: str, to_status: str, body_file: str | None, as_json: bool) -> None:
    """Propose a done / deferred_next_sprint status for human acceptance."""
    note = read_optional_body(body_file, as_json)
    data = http.send(
        "POST", f"/api/items/{item_id}/propose-status",
        as_json=as_json, json_body={"to": to_status, "note": note},
    )
    http.emit(data, as_json, f"{data['id']} status proposal {to_status}")


# --- sprint group ---


@main.group("sprint")
def sprint() -> None:
    """Sprint verbs."""


@sprint.command("show")
@json_option
def sprint_show(as_json: bool) -> None:
    """Show the current sprint view."""
    data = http.send("GET", "/api/sprint/current", as_json=as_json)
    s = data["sprint"]
    human = (
        "no current sprint"
        if s is None
        else f"sprint {s['name']} {s['date_start']}..{s['date_end']}"
    )
    http.emit(data, as_json, human)


# --- idea group ---


@main.group("idea")
def idea() -> None:
    """Idea verbs."""


@idea.command("create")
@click.option("--title", required=True)
@click.option("--project", default=None, help="Vylo | Tribe | Learning | Other.")
@click.option("--body-file", default=None, help="Optional body, or - for stdin.")
@json_option
def idea_create(title: str, project: str | None, body_file: str | None, as_json: bool) -> None:
    """Create an idea."""
    body: dict[str, Any] = {"title": title, "body": read_optional_body(body_file, as_json) or ""}
    if project is not None:
        body["project"] = project
    data = http.send("POST", "/api/ideas", as_json=as_json, json_body=body)
    http.emit(data, as_json, f"{data['id']} {data['title']}")


@idea.command("list")
@json_option
def idea_list(as_json: bool) -> None:
    """List ideas."""
    data = http.send("GET", "/api/ideas", as_json=as_json)
    http.emit(data, as_json, _lines(data["ideas"], lambda i: f"{i['id']} {i['title']}"))


# --- day group ---


@main.group("day")
def day() -> None:
    """Day verbs."""


@day.command("show")
@click.argument("date", required=False)
@json_option
def day_show(date: str | None, as_json: bool) -> None:
    """Show a day (defaults to the current planning date; accepts 'today')."""
    seg = date or "today"
    data = http.send("GET", f"/api/day/{seg}", as_json=as_json)
    http.emit(data, as_json, f"day {data['id']}: {len(data['tickets'])} ticket(s)")


@day.command("add-ticket")
@click.argument("ticket_id")
@click.argument("date", required=False)
@json_option
def day_add_ticket(ticket_id: str, date: str | None, as_json: bool) -> None:
    """Add a ticket to a day (accepts 'today')."""
    seg = date or "today"
    data = http.send(
        "POST", f"/api/day/{seg}/tickets", as_json=as_json, json_body={"ticket_id": ticket_id}
    )
    http.emit(data, as_json, f"day {data['id']}: {len(data['tickets'])} ticket(s)")


@day.command("remove-ticket")
@click.argument("ticket_id")
@click.argument("date", required=False)
@json_option
def day_remove_ticket(ticket_id: str, date: str | None, as_json: bool) -> None:
    """Remove a ticket from a day (deferring; ticket state untouched)."""
    seg = date or "today"
    data = http.send("DELETE", f"/api/day/{seg}/tickets/{ticket_id}", as_json=as_json)
    http.emit(data, as_json, f"day {data['id']}: {len(data['tickets'])} ticket(s)")


# --- link group ---


@main.group("link")
def link() -> None:
    """Link verbs."""


@link.command("add")
@click.argument("from_id")
@click.argument("to_id")
@click.option("--kind", type=click.Choice(_LINK_KINDS), required=True)
@json_option
def link_add(from_id: str, to_id: str, kind: str, as_json: bool) -> None:
    """Add a link."""
    data = http.send(
        "POST", "/api/links", as_json=as_json,
        json_body={"from_id": from_id, "to_id": to_id, "kind": kind},
    )
    http.emit(data, as_json, f"linked {from_id} -{kind}-> {to_id}")


@link.command("rm")
@click.argument("from_id")
@click.argument("to_id")
@click.option("--kind", type=click.Choice(_LINK_KINDS), required=True)
@json_option
def link_rm(from_id: str, to_id: str, kind: str, as_json: bool) -> None:
    """Remove a link."""
    data = http.send(
        "DELETE", "/api/links", as_json=as_json,
        params={"from_id": from_id, "to_id": to_id, "kind": kind},
    )
    http.emit(data, as_json, f"unlinked {from_id} -{kind}-> {to_id}")


# --- queue group ---


@main.group("queue")
def queue() -> None:
    """Derived read-only views (§4.5)."""


@queue.command("approvals")
@json_option
def queue_approvals(as_json: bool) -> None:
    """Pending gating-field / status proposals and needs_review, oldest first."""
    data = http.send("GET", "/api/queues", as_json=as_json)
    section = data["approvals"]
    http.emit(
        {"approvals": section},
        as_json,
        _lines(section, lambda e: f"{e['entity_type']} {e['entity_id']} {e['kind']} {e['title']}"),
    )


@queue.command("overdue")
@json_option
def queue_overdue(as_json: bool) -> None:
    """Tickets/items past deadline and not done/dropped."""
    data = http.send("GET", "/api/queues", as_json=as_json)
    section = data["overdue"]
    http.emit(
        {"overdue": section},
        as_json,
        _lines(
            section,
            lambda e: f"{e['entity_type']} {e['id']} {e['state']} {e['priority']} {e['title']}",
        ),
    )


if __name__ == "__main__":
    main()
