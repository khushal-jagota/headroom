"""One-off current-sprint cutover from a V1 markdown planning tree.

This script intentionally imports only the live current sprint surface:

- sprints/current/sprint-kickoff.md
- sprints/current/sprint-review.md
- sprints/current/sprint-tracking.md
- every sprints/current/daily/YYYY-MM-DD/{overview,tracker,workspace}.md

It does not import deferred.md or ideas.md. The normal seed importer is broader
in one direction (deferred/ideas) and narrower in another (latest daily only),
so this script composes the existing tested parsers and data writers directly.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.seed.contracts import MigrationReport, ParsedTicket, SkippedSection
from planner.seed.importer import _import_items, _import_sprint, _import_tickets
from planner.seed.logic.blocks import split_sections
from planner.seed.logic.kickoff import parse_kickoff, parse_review
from planner.seed.logic.tracking import parse_tracking
from planner.seed.logic.workspace import parse_workspace

# The migration clears the planner records and re-imports the current-sprint surface.
RESET_TABLES = (
    "day_tickets",
    "links",
    "tickets",
    "sprint_items",
    "ideas",
    "days",
    "sprints",
)
REPO_ROOT = Path(__file__).resolve().parent.parent
DAY_OVERVIEW_FIELDS = frozenset({"Brief Take", "Watchout", "If Today Lands", "If Stage 2 Lands"})


@dataclass(frozen=True)
class ParsedDay:
    date: str
    day_id: str
    focus: str
    brief_take: str
    watchout: str
    if_today_lands: str
    notes: str
    ticket_keys: list[str]


@dataclass
class CurrentSprintParse:
    sprint_item_count: int
    unique_ticket_count: int
    day_count: int
    day_ticket_count: int
    skipped: list[SkippedSection] = field(default_factory=list)
    ticket_aliases: list[str] = field(default_factory=list)


@dataclass
class AppliedSummary:
    backup_path: str
    sprints: int
    sprint_items: int
    tickets: int
    days: int
    day_tickets: int
    links: int


@dataclass
class ScriptSummary:
    mode: str
    source: str
    db_path: str
    parsed: CurrentSprintParse
    applied: AppliedSummary | None = None


def _strip_blank_edges(text: str) -> str:
    lines = text.split("\n")
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(lines)


def _section_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    _preamble, sections = split_sections(path.read_text())
    return {heading: _strip_blank_edges(body) for heading, body in sections}


def _ticket_key(ticket: ParsedTicket) -> str:
    return ticket.alias if ticket.alias is not None else f"title:{ticket.title}"


def _valid_date_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        datetime.strptime(path.name, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _notes_from_sections(tracker: dict[str, str], overview: dict[str, str]) -> str:
    parts: list[str] = []
    for heading, body in overview.items():
        if heading in DAY_OVERVIEW_FIELDS or not body:
            continue
        parts.append(f"## Overview: {heading}\n\n{body}")
    for heading, body in tracker.items():
        if heading == "Focus" or not body:
            continue
        label = "Tracker Notes" if heading == "Notes" else f"Tracker: {heading}"
        parts.append(f"## {label}\n\n{body}")
    return "\n\n".join(parts)


def _resolve_db_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _resolve_configured_db_path(raw: str | None) -> tuple[Path, Any]:
    config = load_config(str(REPO_ROOT / "config.yaml"))
    selected = raw if raw is not None else config.db_path
    return _resolve_db_path(selected), config


def _daily_dirs(current: Path) -> list[Path]:
    daily_dir = current / "daily"
    if not daily_dir.exists():
        return []
    return sorted(
        (p for p in daily_dir.iterdir() if _valid_date_dir(p)),
        key=lambda p: p.name,
    )


def parse_current_sprint(
    source: Path,
) -> tuple[MigrationReport, list[ParsedDay], CurrentSprintParse]:
    current = source / "sprints" / "current"
    kickoff_path = current / "sprint-kickoff.md"
    tracking_path = current / "sprint-tracking.md"
    review_path = current / "sprint-review.md"
    if not kickoff_path.exists():
        raise SystemExit(f"missing current sprint kickoff: {kickoff_path}")
    if not tracking_path.exists():
        raise SystemExit(f"missing current sprint tracking: {tracking_path}")

    report = MigrationReport()
    sprint, skipped = parse_kickoff(kickoff_path.read_text(), _rel(source, kickoff_path))
    report.skipped.extend(skipped)
    tracking_items, skipped = parse_tracking(tracking_path.read_text(), _rel(source, tracking_path))
    report.skipped.extend(skipped)
    review: dict[str, str] = {}
    if review_path.exists():
        review, skipped = parse_review(review_path.read_text(), _rel(source, review_path))
        report.skipped.extend(skipped)

    item_titles = [item.title for item in tracking_items]
    tickets_by_key: dict[str, ParsedTicket] = {}
    parsed_days: list[ParsedDay] = []
    for day_dir in _daily_dirs(current):
        workspace_path = day_dir / "workspace.md"
        day_tickets: list[ParsedTicket] = []
        if workspace_path.exists():
            day_tickets, skipped = parse_workspace(
                workspace_path.read_text(), _rel(source, workspace_path), item_titles
            )
            report.skipped.extend(skipped)
        ticket_keys: list[str] = []
        for ticket in day_tickets:
            key = _ticket_key(ticket)
            tickets_by_key[key] = ticket
            ticket_keys.append(key)
        overview = _section_map(day_dir / "overview.md")
        tracker = _section_map(day_dir / "tracker.md")
        parsed_days.append(
            ParsedDay(
                date=day_dir.name,
                day_id=f"day_{day_dir.name}",
                focus=tracker.get("Focus", ""),
                brief_take=overview.get("Brief Take", ""),
                watchout=overview.get("Watchout", ""),
                if_today_lands=overview.get("If Today Lands")
                or overview.get("If Stage 2 Lands", ""),
                notes=_notes_from_sections(tracker, overview),
                ticket_keys=ticket_keys,
            )
        )

    # Store the current-sprint parsed objects on the report for the apply step
    # without changing the public MigrationReport shape.
    cast(Any, report).parsed_sprint = sprint
    cast(Any, report).parsed_review = review
    cast(Any, report).parsed_tracking_items = tracking_items
    cast(Any, report).parsed_tickets = list(tickets_by_key.values())

    parse_summary = CurrentSprintParse(
        sprint_item_count=len(tracking_items),
        unique_ticket_count=len(tickets_by_key),
        day_count=len(parsed_days),
        day_ticket_count=sum(len(day.ticket_keys) for day in parsed_days),
        skipped=report.skipped,
        ticket_aliases=sorted(key for key in tickets_by_key if not key.startswith("title:")),
    )
    return report, parsed_days, parse_summary


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _backup_database(conn: sqlite3.Connection, db_path: Path) -> Path:
    backup_dir = db_path.parent / "migration-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.name}.{stamp}.bak"
    backup_conn = sqlite3.connect(backup_path)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    return backup_path


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _ticket_ids_by_key(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT id, alias, title FROM tickets").fetchall()
    result: dict[str, str] = {}
    for row in rows:
        alias = row["alias"]
        key = alias if alias is not None else f"title:{row['title']}"
        result[key] = row["id"]
    return result


def _reset_tables(conn: sqlite3.Connection, tables: Iterable[str]) -> None:
    for table in tables:
        conn.execute(f"DELETE FROM {table}")


def apply_current_sprint(
    conn: sqlite3.Connection,
    report: MigrationReport,
    parsed_days: list[ParsedDay],
    now: int,
    backup_path: Path,
) -> AppliedSummary:
    sprint = cast(Any, report).parsed_sprint
    review = cast(Any, report).parsed_review
    tracking_items = cast(Any, report).parsed_tracking_items
    tickets = cast(Any, report).parsed_tickets

    conn.execute("BEGIN IMMEDIATE")
    try:
        _reset_tables(conn, RESET_TABLES)
        sprint_id = _import_sprint(conn, sprint, review, report, now)
        items_by_title: dict[str, str] = {}
        _import_items(conn, tracking_items, sprint_id, items_by_title, report, now, deferred=False)
        _import_tickets(conn, tickets, sprint_id, items_by_title, report, now)
        ticket_ids = _ticket_ids_by_key(conn)
        for day in parsed_days:
            days_data.materialize_day(conn, day.day_id, now)
            for field_name, value in (
                ("focus", day.focus),
                ("brief_take", day.brief_take),
                ("watchout", day.watchout),
                ("if_today_lands", day.if_today_lands),
                ("notes", day.notes),
            ):
                if value:
                    days_data.set_day_field(conn, day.day_id, field_name, value, now)
            for key in day.ticket_keys:
                ticket_id = ticket_ids[key]
                days_data.add_day_ticket(conn, day.day_id, ticket_id, now)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return AppliedSummary(
        backup_path=str(backup_path),
        sprints=_count(conn, "sprints"),
        sprint_items=_count(conn, "sprint_items"),
        tickets=_count(conn, "tickets"),
        days=_count(conn, "days"),
        day_tickets=_count(conn, "day_tickets"),
        links=_count(conn, "links"),
    )


def _json_default(value: object) -> object:
    if isinstance(value, SkippedSection):
        return asdict(value)
    raise TypeError(f"cannot JSON encode {type(value).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="V1 markdown planning root.")
    parser.add_argument("--db-path", default=None, help="Target v2 SQLite DB; defaults to config.")
    parser.add_argument(
        "--apply", action="store_true", help="Backup, clear planner records, and import into DB."
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    db_path, config = _resolve_configured_db_path(args.db_path)
    report, parsed_days, parse_summary = parse_current_sprint(source)

    applied: AppliedSummary | None = None
    if args.apply:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(str(db_path), config.db_busy_timeout_ms)
        try:
            backup_path = _backup_database(conn, db_path)
            create_schema(conn)
            now = build_clock(config).now_unix()
            applied = apply_current_sprint(conn, report, parsed_days, now, backup_path)
        finally:
            conn.close()

    summary = ScriptSummary(
        mode="apply" if args.apply else "dry-run",
        source=str(source),
        db_path=str(db_path),
        parsed=parse_summary,
        applied=applied,
    )
    print(json.dumps(asdict(summary), default=_json_default, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
