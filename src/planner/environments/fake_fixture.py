"""Canonical fake data fixture for non-production environment instances."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from planner.core import db
from planner.core.clock import Clock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind, Priority
from planner.days import data as days_data
from planner.files.logic.paths import ticket_files_root
from planner.projects import data as projects_data
from planner.projects.contracts import Project
from planner.sprints import data as sprints_data
from planner.sprints.contracts import SprintItem
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, Ticket, TicketEdit

FAKE_FIXTURE_VERSION = "fake-fixture-v1"


@dataclass(frozen=True)
class FakeFixtureReport:
    fixture_version: str
    logical_summary: dict[str, int]
    logical_titles: dict[str, tuple[str, ...]]
    generated_ids: frozenset[str]
    managed_file_relative_paths: tuple[Path, ...]


class _FixedClock(Clock):
    def __init__(self, now: int) -> None:
        self._now = now

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._now, tz=UTC)

    def now_unix(self) -> int:
        return self._now


def build_fake_environment_database(db_path: Path, *, now: int) -> FakeFixtureReport:
    """Build a small fictional workspace using the current schema and domain writers."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(str(db_path))
    clock = _FixedClock(now)
    generated_ids: set[str] = set()
    try:
        db.create_schema(conn)
        projects = _create_projects(conn, now=now)
        sprint = sprints_data.create_sprint(
            conn,
            name="Fictional July Systems Sprint",
            date_start="2026-07-06",
            date_end="2026-07-17",
            kickoff=(
                "## Limiting factor\n\nKeep the fake workspace small enough to inspect.\n\n"
                "## Supports\n\nRepresentative tickets, day placement, and managed files.\n\n"
                "## Premortem\n\nThe useful failure is accidental coupling between instances."
            ),
            primary_bet="Prove isolated environments without touching live state.",
            clock=clock,
        )
        generated_ids.add(sprint.id)
        sprints_data.update_sprint_field(
            conn,
            sprint.id,
            "review",
            "Fake environments materialize predictably and independently.",
            clock=clock,
        )
        items = _create_items(
            conn,
            sprint_id=sprint.id,
            project_ids=(projects[0].id, projects[1].id),
            clock=clock,
        )
        generated_ids.update(item.id for item in items)
        day_id = "day_2026-07-10"
        days_data.set_day_field(
            conn,
            day_id,
            "focus",
            "Exercise the isolated runtime fixture.",
            now,
        )
        days_data.set_day_field(
            conn,
            day_id,
            "notes",
            "This is fictional non-production planning data.",
            now,
        )
        tickets = _create_tickets(
            conn,
            sprint_id=sprint.id,
            sprint_item_ids=(items[0].id, items[1].id),
            project_id=projects[0].id,
            now=now,
        )
        generated_ids.update(ticket.id for ticket in tickets)
        for ticket in tickets:
            days_data.add_day_ticket(conn, day_id, ticket.id, now)
        managed_file_relative_paths = _write_managed_files(db_path, tickets[0].id)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    return FakeFixtureReport(
        fixture_version=FAKE_FIXTURE_VERSION,
        logical_summary={
            "projects": 2,
            "sprints": 1,
            "sprint_items": 2,
            "days": 1,
            "tickets": 4,
            "managed_files": len(managed_file_relative_paths),
        },
        logical_titles={
            "projects": tuple(project.name for project in projects),
            "sprint_items": tuple(item.title for item in items),
            "tickets": tuple(ticket.title for ticket in tickets),
        },
        generated_ids=frozenset(generated_ids),
        managed_file_relative_paths=managed_file_relative_paths,
    )


def _create_projects(conn: sqlite3.Connection, *, now: int) -> tuple[Project, Project]:
    return (
        projects_data.create_project(
            conn,
            name="Northstar Demo",
            priority=Priority.P1,
            summary="Fictional product workspace for environment isolation.",
            now=now,
        ),
        projects_data.create_project(
            conn,
            name="Harbor Ops",
            priority=Priority.P2,
            summary="Fictional operations workspace for staging testing.",
            now=now,
        ),
    )


def _create_items(
    conn: sqlite3.Connection,
    *,
    sprint_id: str,
    project_ids: tuple[str, str],
    clock: Clock,
) -> tuple[SprintItem, SprintItem]:
    items = (
        sprints_data.create_item(
            conn,
            title="Prepare isolated runtime story",
            body="Small representative plan for fictional staging work.",
            priority=Priority.P1,
            project_id=project_ids[0],
            clock=clock,
        ),
        sprints_data.create_item(
            conn,
            title="Review fake environment behavior",
            body="Fictional review item with child tickets in multiple states.",
            priority=Priority.P2,
            project_id=project_ids[1],
            clock=clock,
        ),
    )
    for item in items:
        conn.execute(
            "INSERT INTO sprint_outcomes(sprint_id,outcome_id) VALUES (?,?)", (sprint_id, item.id)
        )
    return items


def _create_tickets(
    conn: sqlite3.Connection,
    *,
    sprint_id: str,
    sprint_item_ids: tuple[str, str],
    project_id: str,
    now: int,
) -> tuple[Ticket, Ticket, Ticket, Ticket]:
    coding = tickets_actions.create_ticket(
        conn,
        title="Implement fake environment materialization",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="Build fictional state only.",
        sprint_item_id=sprint_item_ids[0],
        sprint_id=sprint_id,
        worker_type="coding",
        stated_ceiling="needs_approach",
    )
    coding = tickets_data.file_current_proposal(
        conn,
        coding.id,
        body="Staging data is isolated from live data.",
        principal=Principal(PrincipalKind.ticket, coding.id),
        now=now,
    )
    coding = tickets_data.edit_ticket(
        conn,
        coding.id,
        edit=TicketEdit(recap="The fake fixture has a settled kickoff and success note."),
        title_max_chars=TITLE_MAX_CHARS,
        principal=Principal(PrincipalKind.ticket, coding.id),
        now=now,
    )
    new_worker = tickets_actions.create_ticket(
        conn,
        title="Sketch fictional specialist onboarding",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="Invent a representative worker without creating registry rows.",
        sprint_item_id=sprint_item_ids[0],
        sprint_id=sprint_id,
        worker_type="new_worker",
        stated_ceiling="needs_stages",
    )
    new_worker = tickets_data.file_current_proposal(
        conn,
        new_worker.id,
        body="Use existing registered Worker types only.",
        principal=Principal(PrincipalKind.ticket, new_worker.id),
        now=now,
    )
    new_worker = tickets_data.edit_ticket(
        conn,
        new_worker.id,
        edit=TicketEdit(recap="Onboarding is represented by a current registry type."),
        title_max_chars=TITLE_MAX_CHARS,
        principal=Principal(PrincipalKind.ticket, new_worker.id),
        now=now,
    )
    exploration = tickets_actions.create_ticket(
        conn,
        title="Compare staging reset outcomes",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="Inspect reset behavior in fictional staging.",
        sprint_item_id=sprint_item_ids[1],
        sprint_id=sprint_id,
        worker_type="exploration",
        stated_ceiling="needs_research_plan",
    )
    exploration = tickets_data.file_current_proposal(
        conn,
        exploration.id,
        body="The reset should replace data only for that instance.",
        principal=Principal(PrincipalKind.ticket, exploration.id),
        now=now,
    )
    exploration = tickets_data.edit_ticket(
        conn,
        exploration.id,
        edit=TicketEdit(recap="Exploration is ready for a research plan."),
        title_max_chars=TITLE_MAX_CHARS,
        principal=Principal(PrincipalKind.ticket, exploration.id),
        now=now,
    )
    initiative = tickets_actions.create_ticket(
        conn,
        title="Draft fictional initiative outline",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="Keep this pending to show approval state.",
        project_id=project_id,
        priority=Priority.P2,
        sprint_id=sprint_id,
        worker_type="initiative_planning",
    )
    tickets_data.mark_ticket_errored(
        conn,
        exploration.id,
        now=now,
    )
    return (
        coding,
        new_worker,
        tickets_data.read_ticket(conn, exploration.id),
        initiative,
    )


def _write_managed_files(
    db_path: Path,
    ticket_id: str,
) -> tuple[Path, ...]:
    ticket_root = ticket_files_root(db_path) / ticket_id
    ticket_root.mkdir(parents=True, exist_ok=True)
    ticket_file = ticket_root / "fixture-note.md"
    ticket_file.write_text(
        "# Fictional Managed Note\n\nThis file belongs only to the fake fixture.\n",
        encoding="utf-8",
    )

    image_file = ticket_root / "placeholder.png"
    image_file.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\xf6\x178U"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return (
        Path("tickets") / ticket_id / "fixture-note.md",
        Path("tickets") / ticket_id / "placeholder.png",
    )
