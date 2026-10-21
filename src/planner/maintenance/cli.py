"""Maintenance bypasses HTTP and runtime initialization; historical replay is explicit."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import click

from planner.conversation.retention.contracts import RetentionReport
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
        for key in ("PLAN_TICKET_ID", "PLAN_MANAGER_ID", "PLAN_AGENT_KEY")
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


@maintenance.group("diagnostic-history")
def diagnostic_history() -> None:
    """Read-only reports and explicit owner-run offline history maintenance."""


def _report_from_json(path: Path) -> RetentionReport:
    from planner.conversation.retention.contracts import (
        ConversationRetentionDecision,
        RemovalCandidate,
        RetentionReport,
    )

    body = json.loads(path.read_text())
    body["command"] = tuple(body["command"])
    body["decisions"] = tuple(
        ConversationRetentionDecision(
            conversation_id=decision["conversation_id"],
            hold_reasons=tuple(decision["hold_reasons"]),
            candidates=tuple(RemovalCandidate(**candidate) for candidate in decision["candidates"]),
            total_event_count=decision["total_event_count"],
        )
        for decision in body["decisions"]
    )
    return RetentionReport(**body)


def _write_private_json(path: Path, body: object) -> None:
    with path.open("x", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        json.dump(body, output, indent=2, ensure_ascii=False)
        output.write("\n")


@diagnostic_history.command("report")
@click.option("--database", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--as-of-unix", required=True, type=int)
@click.option("--output", required=True, type=click.Path(path_type=Path))
def diagnostic_report(database: Path, as_of_unix: int, output: Path) -> None:
    """Read one consistent snapshot. No migration or runtime initialization occurs."""
    import sys
    from dataclasses import asdict

    from planner.conversation.retention.data import read_report

    try:
        report = read_report(database, as_of_unix=as_of_unix, command=tuple(sys.argv))
        _write_private_json(output, asdict(report))
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Private removal report: {output}")


def _diagnostic_mutation[MaintenanceResult](
    database: Path, action: Callable[[], MaintenanceResult]
) -> MaintenanceResult:
    from planner.maintenance.diagnostic_history import require_owner

    require_owner()
    launch_root = resolve_planner_launch_root()
    config = load_config(os.environ.get("PLAN_CONFIG_PATH", str(launch_root / "config.yaml")))
    if Path(config.db_path).expanduser().resolve() != database.expanduser().resolve():
        raise RuntimeError(
            "Selected database differs from configuration. Set PLAN_DB_PATH explicitly"
        )
    explicit = os.environ.get("PLAN_SERVER_LIFECYCLE_LEASE_PATH", "").strip()
    lease = PortScopedServerLifecycleLease(
        Path(explicit).resolve() if explicit else resolve_server_lifecycle_lease_path(config.port),
        config.port,
    )
    lease.acquire()
    try:
        return action()
    finally:
        lease.release()


@diagnostic_history.command("apply")
@click.option("--database", required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--report", "report_path", required=True, type=click.Path(exists=True, path_type=Path)
)
@click.option(
    "--yes", is_flag=True, required=True, help="Permanently delete the approved report's rows."
)
def diagnostic_apply(database: Path, report_path: Path, yes: bool) -> None:
    """Stop Panels first. Apply never compacts or chooses a new cutoff."""
    from dataclasses import asdict

    from planner.maintenance.diagnostic_history import apply_report

    try:
        report = _report_from_json(report_path)
        result = _diagnostic_mutation(
            database, lambda: apply_report(database, report, confirmed=yes)
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(asdict(result)))


@diagnostic_history.command("reclaim")
@click.option("--database", required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--yes", is_flag=True, required=True, help="Compact without selecting or deleting history."
)
def diagnostic_reclaim(database: Path, yes: bool) -> None:
    """Stop Panels first. Compaction preserves every logical database row."""
    from dataclasses import asdict

    from planner.maintenance.diagnostic_history import reclaim_database

    try:
        result = _diagnostic_mutation(database, lambda: reclaim_database(database, confirmed=yes))
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(asdict(result)))


@diagnostic_history.command("measure")
@click.option("--database", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--as-of-unix", required=True, type=int)
@click.option("--output-dir", required=True, type=click.Path(path_type=Path))
def diagnostic_measure(database: Path, as_of_unix: int, output_dir: Path) -> None:
    """Measure on fresh private copies. The source opens only in SQLite read-only mode."""
    import sys

    from planner.maintenance.diagnostic_history_measure import measure_fresh_copy

    try:
        result = measure_fresh_copy(
            database, output_dir, as_of_unix=as_of_unix, command=tuple(sys.argv)
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result))
