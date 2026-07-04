"""The `plan` CLI (§8). A single entry point speaking HTTP to the server. The CLI
is for agents and developer debugging; the human operates through the UI. No
resolution verbs exist here (accept/approve/grant/unblock/day-plan are UI/API
only).

Every verb supports --json (machine output; exit codes 0 success, 1
validation/domain error, 2 connection error). Long text arrives via
`--body-file <path>` where `-` reads stdin — body text is never an inline
argument. In T01 every handler except `serve` raises NotImplementedError after
argument parsing, so the full verb tree renders under --help; the HTTP calls land
in stage 4."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

import click

_PRIORITIES = ["P0", "P1", "P2", "P3"]
_FIELDS = ["success", "approach", "plan", "result"]
_LINK_KINDS = ["belongs_to", "parent_child", "blocks", "relates"]
_TICKET_ID_ENV = "PLAN_TICKET_ID"


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


# --- seed ---


@main.command("seed")
@click.option("--source", default=None, help="Path to a markdown planning directory.")
@click.option("--demo", is_flag=True, default=False, help="Create the deterministic demo dataset.")
@json_option
def seed(source: str | None, demo: bool, as_json: bool) -> None:
    """Import a markdown planning directory, or create a demo dataset (§12)."""
    raise NotImplementedError


# --- top-level proposal/recap/note verbs ---


@main.command("propose")
@click.argument("field", type=click.Choice(_FIELDS))
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
@json_option
def propose(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    """File or replace a proposal on a field (agent path)."""
    raise NotImplementedError


@main.command("recap")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
@json_option
def recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    """Write a ticket recap (rejected before needs_approach)."""
    raise NotImplementedError


@main.command("note")
@click.argument("field", type=click.Choice(_FIELDS))
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
@json_option
def note(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    """Write a field's notes slot (human or agent, any time)."""
    raise NotImplementedError


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
@click.option("--item", default=None, help="Sprint item id to parent under.")
@json_option
def ticket_create(
    title: str,
    priority: str | None,
    deadline: str | None,
    project: str | None,
    sprint: str | None,
    item: str | None,
    as_json: bool,
) -> None:
    """Create a ticket."""
    raise NotImplementedError


@ticket.command("show")
@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
@json_option
def ticket_show(ticket_id: str | None, as_json: bool) -> None:
    """Show a ticket (defaults to $PLAN_TICKET_ID)."""
    raise NotImplementedError


@ticket.command("list")
@click.option("--state", default=None)
@click.option("--project", default=None)
@click.option("--sprint", default=None)
@json_option
def ticket_list(state: str | None, project: str | None, sprint: str | None, as_json: bool) -> None:
    """List tickets."""
    raise NotImplementedError


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
    """Set priority / deadline / day / sprint (no ceiling or at_cap — grants are human)."""
    raise NotImplementedError


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
    raise NotImplementedError


@item.command("show")
@click.argument("item_id")
@json_option
def item_show(item_id: str, as_json: bool) -> None:
    """Show a sprint item."""
    raise NotImplementedError


@item.command("list")
@click.option("--status", default=None)
@click.option("--project", default=None)
@click.option("--backlog", is_flag=True, default=False, help="Items with no sprint (backlog).")
@json_option
def item_list(status: str | None, project: str | None, backlog: bool, as_json: bool) -> None:
    """List sprint items."""
    raise NotImplementedError


@item.command("set")
@click.argument("item_id")
@click.option("--status", type=click.Choice(["todo", "active", "blocked"]), default=None)
@click.option("--blocked-by", default=None, help="Comma-separated blocker ticket ids.")
@json_option
def item_set(item_id: str, status: str | None, blocked_by: str | None, as_json: bool) -> None:
    """Set an agent-permitted item transition (§3.2)."""
    raise NotImplementedError


@item.command("propose-status")
@click.argument("item_id")
@click.option(
    "--to", "to_status", type=click.Choice(["done", "deferred_next_sprint"]), required=True
)
@click.option("--body-file", default=None, help="Optional rationale body, or - for stdin.")
@json_option
def item_propose_status(item_id: str, to_status: str, body_file: str | None, as_json: bool) -> None:
    """Propose a done / deferred_next_sprint status for human acceptance."""
    raise NotImplementedError


# --- sprint group ---


@main.group("sprint")
def sprint() -> None:
    """Sprint verbs."""


@sprint.command("show")
@json_option
def sprint_show(as_json: bool) -> None:
    """Show the current sprint view."""
    raise NotImplementedError


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
    raise NotImplementedError


@idea.command("list")
@json_option
def idea_list(as_json: bool) -> None:
    """List ideas."""
    raise NotImplementedError


# --- day group ---


@main.group("day")
def day() -> None:
    """Day verbs."""


@day.command("show")
@click.argument("date", required=False)
@json_option
def day_show(date: str | None, as_json: bool) -> None:
    """Show a day (defaults to the current planning date; accepts 'today')."""
    raise NotImplementedError


@day.command("add-ticket")
@click.argument("ticket_id")
@click.argument("date", required=False)
@json_option
def day_add_ticket(ticket_id: str, date: str | None, as_json: bool) -> None:
    """Add a ticket to a day (accepts 'today')."""
    raise NotImplementedError


@day.command("remove-ticket")
@click.argument("ticket_id")
@click.argument("date", required=False)
@json_option
def day_remove_ticket(ticket_id: str, date: str | None, as_json: bool) -> None:
    """Remove a ticket from a day (deferring; ticket state untouched)."""
    raise NotImplementedError


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
    raise NotImplementedError


@link.command("rm")
@click.argument("from_id")
@click.argument("to_id")
@click.option("--kind", type=click.Choice(_LINK_KINDS), required=True)
@json_option
def link_rm(from_id: str, to_id: str, kind: str, as_json: bool) -> None:
    """Remove a link."""
    raise NotImplementedError


# --- run group ---


@main.group("run")
def run() -> None:
    """Run verbs (claim env from $PLAN_RUN_ID / $PLAN_CLAIM)."""


@run.command("heartbeat")
@json_option
def run_heartbeat(as_json: bool) -> None:
    """Extend the claim by one TTL."""
    raise NotImplementedError


@run.command("close")
@click.option("--outcome", type=click.Choice(["done", "blocked"]), required=True)
@click.option("--summary", default=None, help="Path to the summary body, or - for stdin.")
@json_option
def run_close(outcome: str, summary: str | None, as_json: bool) -> None:
    """Close the run with a terminal outcome."""
    raise NotImplementedError


# --- queue group ---


@main.group("queue")
def queue() -> None:
    """Derived read-only views (§4.5)."""


@queue.command("approvals")
@json_option
def queue_approvals(as_json: bool) -> None:
    """Pending gating-field / status proposals and needs_review, oldest first."""
    raise NotImplementedError


@queue.command("pickup")
@json_option
def queue_pickup(as_json: bool) -> None:
    """Dispatcher-eligible tickets (§7.2)."""
    raise NotImplementedError


@queue.command("overdue")
@json_option
def queue_overdue(as_json: bool) -> None:
    """Tickets/items past deadline and not done/dropped."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
