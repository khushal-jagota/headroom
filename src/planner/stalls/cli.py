"""`panels stalls`: the stall watchdog, run by panels-stall-watchdog.timer."""

from __future__ import annotations

import os
from pathlib import Path

import click

from planner.executor.clock import now_ms


@click.group("stalls", hidden=True)
def stalls_group() -> None:
    """Alarms on stalled Panels work."""


@stalls_group.command("watchdog")
@click.option("--db", "db_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--state-dir", required=True, type=click.Path(file_okay=False, path_type=Path))
def watchdog(db_path: str, state_dir: Path) -> None:
    """Alarm once on each open stall finding and on a stale checker heartbeat.

    Reads the database read-only. Push settings come from the live configuration
    (PLAN_CONFIG_PATH and the PLAN_APNS_* environment); the Chief message goes to the
    local server on the configured port.
    """
    from planner.core.config import load_config
    from planner.notifications.apns import TokenAPNsAdapter, canonical_server_origin
    from planner.notifications.web_push import StandardsWebPushAdapter
    from planner.stalls.alarms import message_chief
    from planner.stalls.watchdog import run_watchdog

    config = load_config(os.environ.get("PLAN_CONFIG_PATH"))
    origin = canonical_server_origin(config.trusted_ingress_canonical_origin)
    apns = TokenAPNsAdapter(config.apns) if config.apns is not None and origin else None
    server_url = f"http://127.0.0.1:{config.port}"
    try:
        run = run_watchdog(
            db_path,
            state_dir,
            now_ms=now_ms(),
            web_push=StandardsWebPushAdapter(),
            apns=apns,
            push_origin=origin,
            message_chief=lambda text: message_chief(text, server_url=server_url),
        )
    finally:
        if apns is not None:
            apns.close()
    if run.chief_accepted:
        chief = "accepted"
    else:
        chief = "not reached" if run.raised else "not needed"
    click.echo(
        f"{len(run.due)} due, {len(run.raised)} raised, "
        f"{run.pushes_delivered} pushes delivered, Chief {chief}"
    )
