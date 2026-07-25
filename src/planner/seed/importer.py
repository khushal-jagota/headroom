"""The seed import pipeline: parse the source tree, land rows behind one
transaction, and report counts + every skipped section. Self-contained direct
SQL against the core DDL (canonical writers do not exist at build time);
idempotent by alias (tickets) / title (items, ideas) / name (sprints)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import ID_PREFIXES, new_id
from planner.projects import data as projects_data
from planner.projects.contracts import Project
from planner.seed.contracts import (
    PROJECT_NAMES,
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
from planner.worker_settings import service as worker_settings_service
from planner.worker_types.configuration import configured_employee_runtime_definitions
from planner.worker_types.contracts import WorkerTypeDefinition


def seed_from_source(
    conn: sqlite3.Connection,
    source_dir: str | Path,
    *,
    worker_type: str,
    employee_backend: str | None = None,
    now: int,
) -> MigrationReport:
    """now is unix seconds from the caller's clock (the app clock in the server,
    a fixed instant in tests) — the importer never reads wall time itself (§13)."""
    runtime_definitions = configured_employee_runtime_definitions()
    worker_type_definition = runtime_definitions.worker_type_registry.require(worker_type)
    selected_employee_backend = runtime_definitions.employee_backend_catalog.require_registered(
        employee_backend
        if employee_backend is not None
        else worker_type_definition.worker_profile.default_employee_backend
    )
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
            selected_employee_backend,
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
        project = _resolve_or_materialize_legacy_project(conn, item.project, now)
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
    employee_backend: str,
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
        default_stage_ownership_mode = None
        if not worker_type_definition.is_terminal(ticket.stage):
            database_parent = worker_settings_service.database_parent_from_connection(conn)
            if database_parent is None:
                raise PlannerError(
                    ErrorCode.validation,
                    "seed import requires a database file to capture stage ownership defaults",
                    {"worker_type": ticket.worker_type, "stage": ticket.stage},
                )
            default_stage_ownership_mode = (
                worker_settings_service.read_stage_default_ownership_for_ticket_entry(
                    database_parent,
                    configured_employee_runtime_definitions().worker_type_registry,
                    ticket.worker_type,
                    ticket.stage,
                )
            )
        conn.execute(
            "INSERT INTO tickets ("
            "id, title, worker_type, employee_backend, stage, priority, deadline, "
            "project_id, sprint_item_id, "
            "sprint_id, recap, ceiling, at_cap, default_stage_ownership_mode, "
            "employee_session_id, alias, fields, created_at, updated_at, "
            "ticket_status_changed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                ticket.title,
                ticket.worker_type,
                employee_backend,
                ticket.stage,
                ticket.priority.value,
                None,
                None,
                sprint_item_id,
                row_sprint_id,
                "",
                ticket.stage,
                "propose",
                (
                    default_stage_ownership_mode.value
                    if default_stage_ownership_mode is not None
                    else None
                ),
                ticket.employee_session_id,
                ticket.alias,
                fields_codec.fields_to_json(fields),
                now,
                now,
                now,
            ),
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
        project = (
            _resolve_or_materialize_legacy_project(conn, idea.project, now)
            if idea.project is not None
            else None
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
        report.ideas += 1


def _resolve_or_materialize_legacy_project(
    conn: sqlite3.Connection, project_name: str, now: int
) -> Project:
    """Resolve recognized v1 vocabulary, recreating a missing row in this transaction."""

    row = conn.execute(
        "SELECT id FROM projects WHERE name = ? COLLATE NOCASE", (project_name,)
    ).fetchone()
    if row is not None:
        return projects_data.read_project(conn, str(row["id"]))
    if project_name not in PROJECT_NAMES:
        raise PlannerError(
            ErrorCode.validation, "invalid legacy project", {"project": project_name}
        )

    base_id = projects_data.project_id_for_name(project_name)
    project_id = base_id
    suffix = 2
    while conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is not None:
        project_id = f"{base_id}_{suffix}"
        suffix += 1
    conn.execute(
        "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (project_id, project_name, now, now),
    )
    return projects_data.read_project(conn, project_id)
