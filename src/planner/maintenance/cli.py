"""Maintenance bypasses HTTP and runtime initialization; historical replay is explicit."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from planner.core.config import load_config
from planner.maintenance.recovery import archive_cancelled_tickets
from planner.server_lifecycle.control import resolve_server_lifecycle_lease_path
from planner.server_lifecycle.supervisor import (
    PortScopedServerLifecycleLease,
    resolve_planner_launch_root,
)


@click.group(hidden=True)
def maintenance() -> None:
    """Owner-run offline database recovery."""


@maintenance.command("archive-cancelled-tickets")
@click.option("--archive-dir", required=True, type=click.Path(path_type=Path))
@click.option(
    "--yes", is_flag=True, required=True, help="Archive and permanently delete the cohort."
)
def archive_cancelled(archive_dir: Path, yes: bool) -> None:
    """Archive the full database privately, then delete legacy cancelled Tickets.

    Stop Panels first. Ticket files under /files/tickets remain untouched. The private
    archive contains all database records, including unrelated records and history.
    Older snapshots are explicitly prepared through the last historical revision
    after the original database is archived, before the cancelled cohort is deleted.
    """
    if os.environ.get("PLAN_ACTOR", "").strip() not in {"", "owner"} or any(
        os.environ.get(key, "").strip()
        for key in ("PLAN_TICKET_ID", "PLAN_SPRINT_ITEM_ID", "PLAN_AGENT_KEY")
    ):
        raise click.ClickException("This offline recovery action requires the owner")
    if not yes:
        raise click.ClickException("Explicit --yes is required")
    launch_root = resolve_planner_launch_root()
    config = load_config(os.environ.get("PLAN_CONFIG_PATH", str(launch_root / "config.yaml")))
    database = Path(config.db_path).expanduser().resolve()
    click.echo(f"Database: {database}")
    explicit = os.environ.get("PLAN_SERVER_LIFECYCLE_LEASE_PATH", "").strip()
    lease = PortScopedServerLifecycleLease(
        Path(explicit).resolve() if explicit else resolve_server_lifecycle_lease_path(config.port),
        config.port,
    )
    try:
        lease.acquire()
        try:
            result = archive_cancelled_tickets(database, archive_dir)
        finally:
            lease.release()
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False))
