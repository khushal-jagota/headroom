"""The seed import pipeline: parse the source tree, land rows behind one
transaction, and report counts + every skipped section. Self-contained direct
SQL against the core DDL (canonical writers do not exist at build time);
idempotent by alias (tickets) / title (items, ideas) / name (sprints)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import append_event
from planner.core.ids import ID_PREFIXES, new_id
from planner.projects import data as projects_data
from planner.seed.contracts import (
    MigrationReport,
    ParsedIdea,
    ParsedItem,
    ParsedSprint,
    ParsedTicket,
    SkippedSection,
)
from planner.seed.logic.blocks import REASON_DAILY, REASON_FILE, excerpt_of
from planner.seed.logic.deferred import parse_deferred
from planner.seed.logic.ideas import parse_ideas
from planner.seed.logic.kickoff import parse_kickoff, parse_review
from planner.seed.logic.latest import pick_latest_daily
from planner.seed.logic.tracking import parse_tracking
from planner.seed.logic.workspace import parse_workspace
from planner.tickets.contracts import FieldSlot, TicketFields
from planner.tickets.logic import fields_codec
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.contracts import WorkerTypeDefinition


def seed_from_source(
    conn: sqlite3.Connection,
    source_dir: str | Path,
    *,
    worker_type: str,
    now: int,
) -> MigrationReport:
    """now is unix seconds from the caller's clock (the app clock in the server,
    a fixed instant in tests) — the importer never reads wall time itself (§13)."""
    worker_type_definition = configured_worker_type_registry().require(worker_type)
    root = Path(source_dir)
    if not root.exists():
        raise PlannerError(ErrorCode.validation, f"seed source directory not found: {root}")
    kickoff_path = root / "sprints" / "current" / "sprint-kickoff.md"
    if not kickoff_path.exists():
        raise PlannerError(
            ErrorCode.validation, "seed source has no sprints/current/sprint-kickoff.md"
        )

    report = MigrationReport()
    skipped = report.skipped

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    sprint, sk = parse_kickoff(kickoff_path.read_text(), rel(kickoff_path))
    skipped.extend(sk)

    tracking_items: list[ParsedItem] = []
    tracking_path = root / "sprints" / "current" / "sprint-tracking.md"
    if tracking_path.exists():
        tracking_items, sk = parse_tracking(tracking_path.read_text(), rel(tracking_path))
        skipped.extend(sk)

    review: dict[str, str] = {}
    review_path = root / "sprints" / "current" / "sprint-review.md"
    if review_path.exists():
        review, sk = parse_review(review_path.read_text(), rel(review_path))
        skipped.extend(sk)

    tickets: list[ParsedTicket] = []
    daily_dir = root / "sprints" / "current" / "daily"
    if daily_dir.exists():
        folders: list[tuple[str, bool]] = [
            (child.name, (child / "workspace.md").exists())
            for child in sorted(daily_dir.iterdir())
            if child.is_dir()
        ]
        chosen, skipped_folders = pick_latest_daily(folders)
        for name in skipped_folders:
            skipped.append(SkippedSection(f"sprints/current/daily/{name}", None, REASON_DAILY, ""))
        if chosen is not None:
            chosen_dir = daily_dir / chosen
            for file in sorted(chosen_dir.iterdir()):
                if file.is_file() and file.name != "workspace.md":
                    skipped.append(
                        SkippedSection(rel(file), None, REASON_FILE, excerpt_of(file.read_text()))
                    )
            ws_path = chosen_dir / "workspace.md"
            if ws_path.exists():
                tickets, sk = parse_workspace(
                    ws_path.read_text(),
                    rel(ws_path),
                    [item.title for item in tracking_items],
                    worker_type=worker_type,
                )
                skipped.extend(sk)

    deferred_items: list[ParsedItem] = []
    deferred_path = root / "deferred.md"
    if deferred_path.exists():
        deferred_items, sk = parse_deferred(deferred_path.read_text(), rel(deferred_path))
        skipped.extend(sk)

    ideas: list[ParsedIdea] = []
    ideas_path = root / "ideas.md"
    if ideas_path.exists():
        ideas, sk = parse_ideas(ideas_path.read_text(), rel(ideas_path))
        skipped.extend(sk)

    conn.execute("BEGIN IMMEDIATE")
    try:
        sprint_id = _import_sprint(conn, sprint, review, report, now)
        items_by_title: dict[str, str] = {}
        _import_items(conn, tracking_items, sprint_id, items_by_title, report, now, deferred=False)
        _import_tickets(
            conn,
            tickets,
            sprint_id,
            items_by_title,
            report,
            now,
            worker_type_definition,
        )
        _import_items(conn, deferred_items, None, items_by_title, report, now, deferred=True)
        _import_ideas(conn, ideas, report, now)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return report


def _import_sprint(
    conn: sqlite3.Connection,
    sprint: ParsedSprint,
    review: dict[str, str],
    report: MigrationReport,
    now: int,
) -> str:
    row = conn.execute("SELECT id FROM sprints WHERE name = ?", (sprint.name,)).fetchone()
    if row is not None:
        report.duplicates_skipped += 1
        return cast(str, row["id"])
    sprint_id = new_id(ID_PREFIXES["sprint"])
    conn.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, limiting_factor, primary_bet, "
        "supports, premortem, outcomes, solo_reflection, joint_discussion, updates_to_thinking, "
        "carry_forward, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sprint_id,
            sprint.name,
            sprint.date_start,
            sprint.date_end,
            sprint.limiting_factor,
            sprint.primary_bet,
            sprint.supports,
            sprint.premortem,
            review.get("outcomes", ""),
            review.get("solo_reflection", ""),
            review.get("joint_discussion", ""),
            review.get("updates_to_thinking", ""),
            review.get("carry_forward", ""),
            now,
            now,
        ),
    )
    append_event(
        conn,
        sprint_id,
        EventKind.sprint_created,
        {
            "name": sprint.name,
            "date_start": sprint.date_start,
            "date_end": sprint.date_end,
            "source": "seed",
        },
        now,
    )
    report.sprints += 1
    return sprint_id


def _import_items(
    conn: sqlite3.Connection,
    items: list[ParsedItem],
    sprint_id: str | None,
    items_by_title: dict[str, str],
    report: MigrationReport,
    now: int,
    deferred: bool,
) -> None:
    for item in items:
        row = conn.execute("SELECT id FROM sprint_items WHERE title = ?", (item.title,)).fetchone()
        if row is not None:
            report.duplicates_skipped += 1
            items_by_title[item.title] = cast(str, row["id"])
            continue
        item_id = new_id(ID_PREFIXES["sprint_item"])
        project = projects_data.resolve_project(
            conn, project_id=None, project_name=item.project, required=True
        )
        assert project is not None
        item_is_deferred = deferred or item.deferred
        item_sprint_id = None if item_is_deferred else sprint_id
        conn.execute(
            "INSERT INTO sprint_items (id, title, body, priority, deadline, project_id, "
            "sprint_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                item.title,
                item.body,
                item.priority.value,
                item.deadline,
                project.id,
                item_sprint_id,
                now,
                now,
            ),
        )
        append_event(
            conn,
            item_id,
            EventKind.sprint_item_created,
            {
                "title": item.title,
                "sprint_id": item_sprint_id,
                "source": "seed",
            },
            now,
        )
        if item_is_deferred:
            report.deferred_items += 1
        else:
            report.sprint_items += 1
        items_by_title[item.title] = item_id


def _import_tickets(
    conn: sqlite3.Connection,
    tickets: list[ParsedTicket],
    sprint_id: str,
    items_by_title: dict[str, str],
    report: MigrationReport,
    now: int,
    worker_type_definition: WorkerTypeDefinition,
) -> None:
    for ticket in tickets:
        if ticket.worker_type != worker_type_definition.worker_type:
            raise PlannerError(
                ErrorCode.validation,
                "parsed ticket worker type does not match selected worker type",
                {
                    "selected_worker_type": worker_type_definition.worker_type,
                    "ticket_worker_type": ticket.worker_type,
                },
            )
        if ticket.alias is not None:
            row = conn.execute("SELECT id FROM tickets WHERE alias = ?", (ticket.alias,)).fetchone()
        else:
            row = conn.execute("SELECT id FROM tickets WHERE title = ?", (ticket.title,)).fetchone()
        if row is not None:
            report.duplicates_skipped += 1
            continue
        ticket_id = new_id(ID_PREFIXES["ticket"])
        item_id = items_by_title.get(ticket.item_title) if ticket.item_title is not None else None
        if item_id is not None:
            sprint_item_id: str | None = item_id
            row_sprint_id: str | None = None
        else:
            sprint_item_id = None
            row_sprint_id = sprint_id
        fields = TicketFields.empty(worker_type_definition.field_ids())
        for field, value in (
            ("kickoff", ticket.body),
            ("success", ticket.success),
            ("approach", ticket.approach),
        ):
            fields = fields_codec.with_slot(fields, field, FieldSlot(value=value))
        worker_type_definition.validate_ticket_position(ticket.stage, ticket.stage)
        conn.execute(
            "INSERT INTO tickets ("
            "id, title, worker_type, stage, priority, deadline, project_id, sprint_item_id, "
            "sprint_id, recap, ceiling, at_cap, "
            "employee_session_id, alias, fields, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                ticket.title,
                ticket.worker_type,
                ticket.stage,
                ticket.priority.value,
                None,
                None,
                sprint_item_id,
                row_sprint_id,
                "",
                ticket.stage,
                "propose",
                ticket.employee_session_id,
                ticket.alias,
                fields_codec.fields_to_json(fields),
                now,
                now,
            ),
        )
        append_event(
            conn,
            ticket_id,
            EventKind.ticket_created,
            {
                "title": ticket.title,
                "stage": ticket.stage,
                "alias": ticket.alias,
                "source": "seed",
            },
            now,
        )
        report.tickets += 1


def _import_ideas(
    conn: sqlite3.Connection,
    ideas: list[ParsedIdea],
    report: MigrationReport,
    now: int,
) -> None:
    for idea in ideas:
        row = conn.execute("SELECT id FROM ideas WHERE title = ?", (idea.title,)).fetchone()
        if row is not None:
            report.duplicates_skipped += 1
            continue
        idea_id = new_id(ID_PREFIXES["idea"])
        project = projects_data.resolve_project(
            conn, project_id=None, project_name=idea.project, required=False
        )
        conn.execute(
            "INSERT INTO ideas (id, title, body, project_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                idea_id,
                idea.title,
                idea.body,
                project.id if project is not None else None,
                now,
                now,
            ),
        )
        append_event(
            conn,
            idea_id,
            EventKind.idea_created,
            {"title": idea.title, "source": "seed"},
            now,
        )
        report.ideas += 1
