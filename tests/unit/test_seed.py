"""Acceptance item 19 for the seed domain: importing the synthetic fixture
yields the exact counts, spot-checked mappings, the designed skip list, and
idempotent re-runs. Plus supporting tests for the pure parser helpers. The reason
strings below are an independent oracle — hard-coded here, never imported from the
parsers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection

from planner.core.contracts import Priority
from planner.seed.contracts import SkippedSection
from planner.seed.importer import seed_from_source
from planner.seed.logic.fieldmap import resolve_priority
from planner.seed.logic.latest import pick_latest_daily
from planner.seed.logic.tracking import parse_tracking
from planner.seed.logic.workspace import match_item_title, parse_workspace

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "planning-md"

# Seeding takes the caller's clock; tests pin one. Noon UTC keeps the import's
# planning date (boundary hour 5) at 2026-07-04 for any sane local timezone.
_FIXED_NOW = int(datetime(2026, 7, 4, 12, 0, tzinfo=UTC).timestamp())

_REASON_DAILY = "not the latest daily folder; only the latest day's workspace.md is imported (R6)"
_REASON_FILE = "file has no migration mapping (only workspace.md is imported from a daily folder)"
_REASON_SECTION = "section has no migration mapping (SPEC 12)"
_REASON_PREAMBLE = "preamble/rules prose; not importable items"
_REASON_NO_READINESS = "ticket bullet has no recognized Readiness value; not imported"
_REASON_TITLE_LONG = "ticket title exceeds 200 characters; not imported"
_REASON_PROSE = "prose inside a bullet section; not an importable item"


def _count(conn: Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _rows_by(conn: Connection, sql: str, key: str) -> dict[str, object]:
    return {row[key]: row for row in conn.execute(sql).fetchall()}


_EXPECTED_SKIPS = [
    SkippedSection("sprints/current/daily/2026-06-10", None, _REASON_DAILY, ""),
    SkippedSection(
        "sprints/current/daily/2026-06-11/overview.md", None, _REASON_FILE,
        "# Overview Date: 2026-06-11 ## Brief Take Latest overview content; "
        "enumerated as skipped, never imported.",
    ),
    SkippedSection(
        "sprints/current/daily/2026-06-11/tracker.md", None, _REASON_FILE,
        "# Daily Date: 2026-06-11 ## Focus Latest tracker content; "
        "enumerated as skipped, never imported.",
    ),
    SkippedSection(
        "sprints/current/daily/2026-06-11/workspace.md", "Necessary Calls", _REASON_SECTION,
        "- Pick one lane for the day and hold it. - Recommended call: "
        "finish the import pipeline before touching polish.",
    ),
    SkippedSection(
        "deferred.md", None, _REASON_PREAMBLE,
        "Important work not in the current sprint. Rules: "
        "- Group by project. - Priority is not urgency.",
    ),
    SkippedSection(
        "ideas.md", None, _REASON_PREAMBLE,
        "Interesting concepts worth exploring later. Rules: "
        "- Keep entries readable standalone.",
    ),
]


def test_a19_seed_fixture_import_counts_mappings_idempotency_and_skip_list(
    tmp_db: Connection,
) -> None:
    report = seed_from_source(tmp_db, FIXTURE, _FIXED_NOW)

    # (1) report counts.
    assert (
        report.sprints, report.sprint_items, report.deferred_items,
        report.tickets, report.ideas, report.links, report.duplicates_skipped,
    ) == (1, 5, 4, 4, 3, 1, 0)

    # (2) DB row counts.
    assert _count(tmp_db, "sprints") == 1
    assert _count(tmp_db, "sprint_items") == 9
    assert _count(tmp_db, "tickets") == 4
    assert _count(tmp_db, "ideas") == 3
    assert _count(tmp_db, "links") == 1
    assert _count(tmp_db, "events") == 18
    default_projects = _rows_by(tmp_db, "SELECT id, name FROM projects", "id")
    assert default_projects["project_vylo"]["name"] == "Vylo"
    assert default_projects["project_tribe"]["name"] == "Tribe"
    assert default_projects["project_learning"]["name"] == "Learning"
    assert default_projects["project_other"]["name"] == "Other"

    # (3) sprint row spot-check.
    sprint = tmp_db.execute(
        "SELECT id, name, date_start, date_end, limiting_factor, outcomes FROM sprints"
    ).fetchone()
    sprint_id = sprint["id"]
    assert sprint["name"] == "Sprint 2026-06-08 to 2026-06-21"
    assert sprint["date_start"] == "2026-06-08"
    assert sprint["date_end"] == "2026-06-21"
    assert sprint["limiting_factor"] == (
        "The importer has no fixture coverage, so every parser change is a guess."
    )
    assert sprint["outcomes"] == "The fixture pack shipped and the importer ran clean twice."

    # (4) item priority/project/body mappings; legacy Deferred imports as backlog.
    items = _rows_by(
        tmp_db,
        "SELECT sprint_items.id, sprint_items.title, "
        "sprint_items.priority, sprint_items.project_id, projects.name AS project, "
        "sprint_items.sprint_id, sprint_items.deadline, sprint_items.body "
        "FROM sprint_items JOIN projects ON projects.id = sprint_items.project_id "
        "WHERE sprint_items.sprint_id IS NOT NULL",
        "title",
    )
    expected = {
        "Write the parser design note.": ("P1", "Learning"),
        "Refit the garden shed.": ("P3", "Other"),
        "Build the import pipeline.": ("P0", "Vylo"),
        "Ship the fixture pack.": ("P2", "Vylo"),
        "Publish the beta changelog.": ("P1", "Tribe"),
    }
    assert set(items) == set(expected)
    for title, (priority, project) in expected.items():
        row = items[title]
        assert row["priority"] == priority
        assert row["project"] == project
        assert row["sprint_id"] == sprint_id
        assert row["deadline"] is None
    assert items["Write the parser design note."]["body"] == (
        "- Cover the tokenizer first.\n  - Then the emitter.\n- Keep examples from the real files."
    )
    backlog = _rows_by(
        tmp_db,
        "SELECT sprint_items.title, sprint_items.priority, sprint_items.project_id, "
        "projects.name AS project, sprint_items.sprint_id "
        "FROM sprint_items JOIN projects ON projects.id = sprint_items.project_id "
        "WHERE sprint_items.sprint_id IS NULL",
        "title",
    )
    assert backlog["Automate the weekly digest."]["priority"] == "P3"
    assert backlog["Automate the weekly digest."]["project"] == "Vylo"

    # (5) ticket Readiness mappings by alias.
    tickets = _rows_by(
        tmp_db,
        "SELECT tickets.id, tickets.alias, tickets.state, tickets.priority, "
        "tickets.chat_session_key, tickets.sprint_item_id, tickets.sprint_id, "
        "tickets.project_id, projects.name AS project, tickets.recap, tickets.ceiling, "
        "tickets.at_cap, tickets.deadline, tickets.fields "
        "FROM tickets LEFT JOIN projects ON projects.id = tickets.project_id",
        "alias",
    )
    assert tickets["ticket-20260611-onboarding-survey"]["state"] == "needs_success"
    assert tickets["ticket-20260611-export-format"]["state"] == "needs_approach"
    assert tickets["ticket-20260611-release-branch"]["state"] == "needs_plan"
    assert tickets["ticket-20260611-import-pipeline"]["state"] == "needs_implementation"
    for row in tickets.values():
        assert row["ceiling"] == row["state"]
        assert row["at_cap"] == "propose"
        assert row["recap"] == ""
        assert row["deadline"] is None

    # (6) chat session key preserved on exactly one ticket.
    assert tickets["ticket-20260611-export-format"]["chat_session_key"] == "20260611_090000_abc123"
    for alias in (
        "ticket-20260611-onboarding-survey",
        "ticket-20260611-release-branch",
        "ticket-20260611-import-pipeline",
    ):
        assert tickets[alias]["chat_session_key"] is None

    # (7) fields JSON.
    onboarding = json.loads(tickets["ticket-20260611-onboarding-survey"]["fields"])
    assert onboarding["success"]["value"] is None
    assert onboarding["kickoff"]["value"] == (
        "Work out what the survey must learn before any UI is sketched.\n"
        "- Project: Vylo\n- Current state: nothing exists yet.\n"
        "- Next: list the three decisions the survey feeds."
    )
    assert onboarding["success"]["user_note"] is None
    export = json.loads(tickets["ticket-20260611-export-format"]["fields"])
    assert export["success"]["value"] == (
        "a one-page format note that a second reader can implement from."
    )
    assert export["kickoff"]["value"] == "- Project: Tribe"
    release = json.loads(tickets["ticket-20260611-release-branch"]["fields"])
    assert release["success"]["value"] == "the branch exists and CI is green on it."
    assert release["approach"]["value"] == (
        "branch from main after the fixture tests pass, then tag."
    )
    assert release["kickoff"]["value"] == "- Project: Vylo"
    pipeline = json.loads(tickets["ticket-20260611-import-pipeline"]["fields"])
    assert pipeline["success"]["value"] == "the fixture import passes twice with zero duplicates."
    assert pipeline["kickoff"]["value"] == (
        "Parse the fixture tree and land rows behind one transaction.\n"
        "- Project: Vylo\n- Boundary: importer only; no CLI wiring yet."
    )
    for parsed in (onboarding, export, release, pipeline):
        assert parsed["plan"] == {"value": None, "proposal": None, "user_note": None}
        assert parsed["implementation"] == {"value": None, "proposal": None, "user_note": None}
        assert parsed["closeout"] == {"value": None, "proposal": None, "user_note": None}
        for slot in parsed.values():
            assert slot["proposal"] is None

    # (8) decoy is absent.
    assert tmp_db.execute(
        "SELECT COUNT(*) FROM tickets WHERE alias = 'ticket-20260610-decoy'"
    ).fetchone()[0] == 0
    assert _count(tmp_db, "tickets") == 4

    # (9) link + parentage.
    item_id = items["Build the import pipeline."]["id"]
    pipeline_row = tickets["ticket-20260611-import-pipeline"]
    assert pipeline_row["sprint_item_id"] == item_id
    assert pipeline_row["project_id"] is None
    assert pipeline_row["project"] is None
    assert pipeline_row["sprint_id"] is None
    link = tmp_db.execute("SELECT from_id, to_id, kind FROM links").fetchone()
    assert (link["from_id"], link["to_id"], link["kind"]) == (
        pipeline_row["id"], item_id, "belongs_to",
    )
    for alias in (
        "ticket-20260611-onboarding-survey",
        "ticket-20260611-export-format",
        "ticket-20260611-release-branch",
    ):
        assert tickets[alias]["sprint_id"] == sprint_id
        assert tickets[alias]["sprint_item_id"] is None

    # (10) deferred items.
    deferred = _rows_by(
        tmp_db,
        "SELECT sprint_items.title, sprint_items.priority, "
        "sprint_items.project_id, projects.name AS project, sprint_items.sprint_id, "
        "sprint_items.deadline, sprint_items.body "
        "FROM sprint_items JOIN projects ON projects.id = sprint_items.project_id "
        "WHERE sprint_items.sprint_id IS NULL",
        "title",
    )
    assert len(deferred) == 4
    expected_deferred = {
        "Automate the weekly digest.": (
            "P3",
            "Vylo",
            "- Next sprint once the event feed settles.",
        ),
        "Tune the retrieval cache.": ("P2", "Vylo", "- Watch the hit rate for a week first."),
        "Rotate the leaked staging key.": ("P0", "Vylo", ""),
        "Read the WAL internals paper.": (
            "P3", "Learning", "- Take notes for the wiki.\n  - File under storage engines.",
        ),
    }
    for title, (priority, project, body) in expected_deferred.items():
        row = deferred[title]
        assert row["priority"] == priority
        assert row["project"] == project
        assert row["body"] == body
        assert row["sprint_id"] is None
        assert row["deadline"] is None

    # (11) ideas.
    ideas = _rows_by(
        tmp_db,
        "SELECT ideas.title, ideas.project_id, projects.name AS project, ideas.body "
        "FROM ideas LEFT JOIN projects ON projects.id = ideas.project_id",
        "title",
    )
    assert ideas["Voice memo inbox for quick capture."]["project"] == "Vylo"
    assert ideas["Voice memo inbox for quick capture."]["body"] == (
        "- Transcribe on arrival and file into the day note."
    )
    assert ideas["Weekly print digest of the learning wiki."]["project"] is None
    assert ideas["Weekly print digest of the learning wiki."]["body"] == ""
    assert ideas["Pair the boundary brief with a spoken audio version."]["project"] is None
    assert ideas["Pair the boundary brief with a spoken audio version."]["body"] == (
        "- Record it right after the plan is accepted."
    )

    # (12) skip list, exact and ordered.
    assert report.skipped == _EXPECTED_SKIPS

    # (13) idempotent re-run: zero new rows, all hits counted.
    report2 = seed_from_source(tmp_db, FIXTURE, _FIXED_NOW)
    assert (
        report2.sprints, report2.sprint_items, report2.deferred_items,
        report2.tickets, report2.ideas, report2.links,
    ) == (0, 0, 0, 0, 0, 0)
    assert report2.duplicates_skipped == 17
    assert report2.skipped == _EXPECTED_SKIPS
    assert _count(tmp_db, "sprints") == 1
    assert _count(tmp_db, "sprint_items") == 9
    assert _count(tmp_db, "tickets") == 4
    assert _count(tmp_db, "ideas") == 3
    assert _count(tmp_db, "links") == 1
    assert _count(tmp_db, "events") == 18


def test_match_item_title_ambiguity() -> None:
    assert match_item_title("A", ["A", "B"]) == "A"
    assert match_item_title("Z", ["A", "B"]) is None
    assert match_item_title("A", ["A", "A"]) is None


def test_resolve_priority_fallback_chain() -> None:
    assert resolve_priority("P0", "high") == Priority.P0
    assert resolve_priority(None, "high") == Priority.P1
    assert resolve_priority(None, "") == Priority.P3
    assert resolve_priority("P9", "LOW") == Priority.P3
    assert resolve_priority("P9", None) == Priority.P3


def test_pick_latest_daily_selection() -> None:
    chosen, skipped = pick_latest_daily(
        [("2026-06-10", True), ("2026-06-11", True), ("2026-06-12", False), ("notes", True)]
    )
    assert chosen == "2026-06-11"
    assert skipped == ["2026-06-10", "2026-06-12", "notes"]
    assert pick_latest_daily([]) == (None, [])


def test_workspace_ticket_missing_readiness_is_enumerated() -> None:
    long_title = "x" * 201
    text = (
        "# Workspace\n\nDate: 2026-06-11\n\n## Tickets\n\n"
        "- Ticket without readiness.\n"
        "  - Ticket ID: ticket-no-readiness\n"
        "  - Priority: P1\n\n"
        f"- {long_title}\n"
        "  - Readiness: Ready\n"
        "  - Priority: P2\n"
    )
    tickets, skipped = parse_workspace(text, "workspace.md", [])
    assert tickets == []
    assert len(skipped) == 2
    assert {section.reason for section in skipped} == {_REASON_NO_READINESS, _REASON_TITLE_LONG}


def test_orphan_prose_in_recognized_section_is_enumerated() -> None:
    text = (
        "# Sprint Tracking\n\nDate range: 2026-06-08 to 2026-06-21\n\n## Todo\n\n"
        "A stray prose paragraph that is not an item.\n\n"
        "- Real item.\n"
        "  - Priority: P2\n"
        "  - Project: Vylo\n"
    )
    items, skipped = parse_tracking(text, "sprint-tracking.md")
    assert [item.title for item in items] == ["Real item."]
    assert skipped == [
        SkippedSection(
            "sprint-tracking.md", "Todo", _REASON_PROSE,
            "A stray prose paragraph that is not an item.",
        )
    ]


def test_workspace_field_continuation_lines_preserved() -> None:
    text = (
        "# Workspace\n\nDate: 2026-06-11\n\n## Tickets\n\n"
        "- Continuation ticket.\n"
        "  - Ticket ID: ticket-continuation\n"
        "  - Readiness: Ready\n"
        "  - Success: first line\n"
        "    second continuation line\n"
    )
    tickets, skipped = parse_workspace(text, "workspace.md", [])
    assert skipped == []
    assert len(tickets) == 1
    assert tickets[0].success == "first line\n    second continuation line"
