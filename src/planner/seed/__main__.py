"""Standalone seed entrypoint for the one-time cutover migration.

Run as: python -m planner.seed --source <dir> --worker-type <id> [--db-path <path>] [--json]

Imports a markdown planning directory into a SQLite database. This is the cutover
script (§12): there is no `/api/seed` route or `panels seed` CLI command, but the
runtime redesign, but the importer (seed_from_source + the parsers) is retained and
driven here. Self-contained — it does not import planner.cli.main."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.seed.importer import seed_from_source


def _format_report(data: dict[str, Any]) -> str:
    lines = [
        f"seed: {data['sprints']} sprint(s), {data['sprint_items']} item(s), "
        f"{data['deferred_items']} deferred, {data['tickets']} ticket(s), "
        f"{data['ideas']} idea(s), {data['links']} link(s), "
        f"{data['duplicates_skipped']} duplicate(s)"
    ]
    skipped = data["skipped"]
    if skipped:
        lines.append(f"skipped: {len(skipped)}")
        for s in skipped:
            lines.append(f"  - {s['source_file']} [{s['heading']}]: {s['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m planner.seed",
        description="Import a markdown planning directory into a planner database.",
    )
    parser.add_argument("--source", required=True, help="Path to a markdown planning directory.")
    parser.add_argument(
        "--worker-type",
        required=True,
        help="Worker type to use for every imported Ticket.",
    )
    parser.add_argument(
        "--employee-backend",
        default=None,
        help="Registered employee backend override for every imported Ticket.",
    )
    parser.add_argument(
        "--db-path", default=None, help="Target SQLite path (defaults to the configured db_path)."
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args(argv)

    config = load_config()
    db_path: str = args.db_path if args.db_path is not None else config.db_path
    conn = connect(db_path, config.db_busy_timeout_ms)
    try:
        create_schema(conn)
        now = build_clock(config).now_unix()
        report = seed_from_source(
            conn,
            args.source,
            worker_type=args.worker_type,
            employee_backend=args.employee_backend,
            now=now,
        )
    finally:
        conn.close()

    data = asdict(report)
    print(json.dumps(data) if args.json else _format_report(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
