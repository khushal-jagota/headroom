"""Acceptance item 19 for the seed domain: importing the synthetic fixture
yields the exact counts, spot-checked mappings, the designed skip list, and
idempotent re-runs. Plus supporting tests for the pure parser helpers. The reason
strings below are an independent oracle — hard-coded here, never imported from the
parsers."""

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace

import pytest
from tests.support.probe import PROBE_EMPLOYEE_BACKEND_CATALOG

from planner.core.contracts import ErrorCode, Priority
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.seed import __main__ as seed_main
from planner.seed.contracts import MigrationReport, SkippedSection
from planner.seed.importer import seed_from_source
from planner.seed.logic.fieldmap import resolve_priority
from planner.seed.logic.latest import pick_latest_daily
from planner.seed.logic.tracking import parse_tracking
from planner.seed.logic.workspace import match_item_title, parse_workspace
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import (
    PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS,
    ConfiguredEmployeeRuntimeDefinitions,
    build_employee_runtime_definitions,
    install_employee_runtime_definitions_for_test,
    restore_employee_runtime_definitions_for_test,
)
from planner.worker_types.contracts import FieldDefinition, StageDefinition
from planner.worker_types.registry import WorkerTypeRegistry

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
        "sprints/current/daily/2026-06-11/overview.md",
        None,
        _REASON_FILE,
        "# Overview Date: 2026-06-11 ## Brief Take Latest overview content; "
        "enumerated as skipped, never imported.",
    ),
    SkippedSection(
        "sprints/current/daily/2026-06-11/tracker.md",
        None,
        _REASON_FILE,
        "# Daily Date: 2026-06-11 ## Focus Latest tracker content; "
        "enumerated as skipped, never imported.",
    ),
    SkippedSection(
        "sprints/current/daily/2026-06-11/workspace.md",
        "Necessary Calls",
        _REASON_SECTION,
        "- Pick one lane for the day and hold it. - Recommended call: "
        "finish the import pipeline before touching polish.",
    ),
    SkippedSection(
        "deferred.md",
        None,
        _REASON_PREAMBLE,
        "Important work not in the current sprint. Rules: "
        "- Group by project. - Priority is not urgency.",
    ),
    SkippedSection(
        "ideas.md",
        None,
        _REASON_PREAMBLE,
        "Interesting concepts worth exploring later. Rules: - Keep entries readable standalone.",
    ),
]


def test_a19_seed_fixture_import_counts_mappings_idempotency_and_skip_list(
    tmp_db: Connection,
) -> None:
    report = seed_from_source(tmp_db, FIXTURE, worker_type="coding", now=_FIXED_NOW)

    # (1) report counts.
    assert (
        report.sprints,
        report.sprint_items,
        report.deferred_items,
        report.tickets,
        report.ideas,
        report.links,
        report.duplicates_skipped,
    ) == (1, 5, 4, 4, 3, 0, 0)

    # (2) DB row counts.
    assert _count(tmp_db, "sprints") == 1
    assert _count(tmp_db, "sprint_items") == 9
    assert _count(tmp_db, "tickets") == 4
    assert _count(tmp_db, "ideas") == 3
    assert _count(tmp_db, "links") == 0
    assert _count(tmp_db, "events") == 17
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
        "SELECT tickets.id, tickets.alias, tickets.stage, tickets.priority, "
        "tickets.worker_type, tickets.employee_session_id, tickets.sprint_item_id, "
        "tickets.sprint_id, "
        "tickets.project_id, projects.name AS project, tickets.recap, tickets.ceiling, "
        "tickets.at_cap, tickets.deadline, tickets.fields "
        "FROM tickets LEFT JOIN projects ON projects.id = tickets.project_id",
        "alias",
    )
    assert tickets["ticket-20260611-onboarding-survey"]["stage"] == "needs_success"
    assert tickets["ticket-20260611-export-format"]["stage"] == "needs_approach"
    assert tickets["ticket-20260611-release-branch"]["stage"] == "needs_plan"
    assert tickets["ticket-20260611-import-pipeline"]["stage"] == "needs_implementation"
    for row in tickets.values():
        assert row["worker_type"] == "coding"
        assert row["ceiling"] == row["stage"]
        assert row["at_cap"] == "propose"
        assert row["recap"] == ""
        assert row["deadline"] is None

    # (6) the historical Chat ID is preserved byte-for-byte as the Employee session id.
    assert (
        tickets["ticket-20260611-export-format"]["employee_session_id"] == "20260611_090000_abc123"
    )
    for alias in (
        "ticket-20260611-onboarding-survey",
        "ticket-20260611-release-branch",
        "ticket-20260611-import-pipeline",
    ):
        assert tickets[alias]["employee_session_id"] is None

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
    assert (
        tmp_db.execute(
            "SELECT COUNT(*) FROM tickets WHERE alias = 'ticket-20260610-decoy'"
        ).fetchone()[0]
        == 0
    )
    assert _count(tmp_db, "tickets") == 4

    # (9) link + parentage.
    item_id = items["Build the import pipeline."]["id"]
    pipeline_row = tickets["ticket-20260611-import-pipeline"]
    assert pipeline_row["sprint_item_id"] == item_id
    assert pipeline_row["project_id"] is None
    assert pipeline_row["project"] is None
    assert pipeline_row["sprint_id"] is None
    assert tmp_db.execute("SELECT 1 FROM links").fetchone() is None
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
            "P3",
            "Learning",
            "- Take notes for the wiki.\n  - File under storage engines.",
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
    report2 = seed_from_source(tmp_db, FIXTURE, worker_type="coding", now=_FIXED_NOW)
    assert (
        report2.sprints,
        report2.sprint_items,
        report2.deferred_items,
        report2.tickets,
        report2.ideas,
        report2.links,
    ) == (0, 0, 0, 0, 0, 0)
    assert report2.duplicates_skipped == 17
    assert report2.skipped == _EXPECTED_SKIPS
    assert _count(tmp_db, "sprints") == 1
    assert _count(tmp_db, "sprint_items") == 9
    assert _count(tmp_db, "tickets") == 4
    assert _count(tmp_db, "ideas") == 3
    assert _count(tmp_db, "links") == 0
    assert _count(tmp_db, "events") == 17


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
    tickets, skipped = parse_workspace(text, "workspace.md", [], worker_type="coding")
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
            "sprint-tracking.md",
            "Todo",
            _REASON_PROSE,
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
    tickets, skipped = parse_workspace(text, "workspace.md", [], worker_type="coding")
    assert skipped == []
    assert len(tickets) == 1
    assert tickets[0].worker_type == "coding"
    assert tickets[0].success == "first line\n    second continuation line"


def test_seed_interfaces_require_keyword_only_worker_type_and_now() -> None:
    seed_parameters = inspect.signature(seed_from_source).parameters
    assert seed_parameters["worker_type"].kind is inspect.Parameter.KEYWORD_ONLY
    assert seed_parameters["worker_type"].default is inspect.Parameter.empty
    assert seed_parameters["now"].kind is inspect.Parameter.KEYWORD_ONLY
    assert seed_parameters["now"].default is inspect.Parameter.empty

    workspace_parameters = inspect.signature(parse_workspace).parameters
    assert workspace_parameters["worker_type"].kind is inspect.Parameter.KEYWORD_ONLY
    assert workspace_parameters["worker_type"].default is inspect.Parameter.empty


def test_standalone_seed_requires_and_forwards_exact_worker_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_config_load() -> None:
        pytest.fail("argparse must reject a missing --worker-type before database work")

    monkeypatch.setattr(seed_main, "load_config", unexpected_config_load)
    with pytest.raises(SystemExit) as raised:
        seed_main.main(["--source", str(FIXTURE)])
    assert raised.value.code == 2

    observed: dict[str, object] = {}

    class FakeConnection:
        def close(self) -> None:
            observed["closed"] = True

    monkeypatch.setattr(
        seed_main,
        "load_config",
        lambda: SimpleNamespace(db_path="unused.db", db_busy_timeout_ms=1),
    )
    monkeypatch.setattr(seed_main, "connect", lambda *_args: FakeConnection())
    monkeypatch.setattr(seed_main, "create_schema", lambda conn: observed.setdefault("conn", conn))
    monkeypatch.setattr(
        seed_main,
        "build_clock",
        lambda _config: SimpleNamespace(now_unix=lambda: _FIXED_NOW),
    )

    def fake_seed(
        conn: object,
        source: str,
        *,
        worker_type: str,
        employee_backend: str | None,
        now: int,
    ) -> MigrationReport:
        observed.update(
            conn=conn,
            source=source,
            worker_type=worker_type,
            employee_backend=employee_backend,
            now=now,
        )
        return MigrationReport()

    monkeypatch.setattr(seed_main, "seed_from_source", fake_seed)
    assert seed_main.main(["--source", str(FIXTURE), "--worker-type", "seed_probe", "--json"]) == 0
    assert observed["worker_type"] == "seed_probe"
    assert observed["employee_backend"] is None
    assert observed["source"] == str(FIXTURE)
    assert observed["now"] == _FIXED_NOW
    assert observed["closed"] is True


def test_seed_backend_default_override_and_unknown_roll_back(
    tmp_path: Path,
) -> None:
    probe_default_coding = replace(
        CODING_WORKER_TYPE_DEFINITION,
        worker_profile=replace(
            CODING_WORKER_TYPE_DEFINITION.worker_profile,
            default_employee_backend="probe-backend",
        ),
    )
    definitions = build_employee_runtime_definitions(
        PROBE_EMPLOYEE_BACKEND_CATALOG,
        worker_type_definitions=(probe_default_coding,),
    )
    previous = install_employee_runtime_definitions_for_test(definitions)
    connections: list[Connection] = []
    try:
        for name in ("default", "override", "rejected"):
            conn = connect(str(tmp_path / f"seed-{name}.db"))
            create_schema(conn)
            connections.append(conn)
        default_conn, override_conn, rejected_conn = connections
        seed_from_source(default_conn, FIXTURE, worker_type="coding", now=_FIXED_NOW)
        seed_from_source(
            override_conn,
            FIXTURE,
            worker_type="coding",
            employee_backend="hermes",
            now=_FIXED_NOW,
        )
        with pytest.raises(PlannerError) as raised:
            seed_from_source(
                rejected_conn,
                FIXTURE,
                worker_type="coding",
                employee_backend="missing-backend",
                now=_FIXED_NOW,
            )

        assert {
            str(row["employee_backend"])
            for row in default_conn.execute("SELECT employee_backend FROM tickets")
        } == {"probe-backend"}
        assert {
            str(row["employee_backend"])
            for row in override_conn.execute("SELECT employee_backend FROM tickets")
        } == {"hermes"}
        assert {
            json.loads(str(row["payload"]))["employee_backend"]
            for row in default_conn.execute(
                "SELECT payload FROM events WHERE kind = 'ticket_created'"
            )
        } == {"probe-backend"}
        assert raised.value.code is ErrorCode.validation
        assert tuple(
            rejected_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sprints", "sprint_items", "tickets", "ideas", "events")
        ) == (0, 0, 0, 0, 0)
    finally:
        for conn in connections:
            conn.close()
        restore_employee_runtime_definitions_for_test(previous)


def test_unknown_seed_worker_type_fails_before_any_import_write(tmp_db: Connection) -> None:
    before = {
        table: _count(tmp_db, table)
        for table in ("sprints", "sprint_items", "tickets", "ideas", "events")
    }
    with pytest.raises(PlannerError) as raised:
        seed_from_source(tmp_db, FIXTURE, worker_type="ghost", now=_FIXED_NOW)
    assert raised.value.code is ErrorCode.not_found
    assert raised.value.detail == {"worker_type": "ghost"}
    assert {
        table: _count(tmp_db, table)
        for table in ("sprints", "sprint_items", "tickets", "ideas", "events")
    } == before


def test_seed_fields_follow_the_explicit_registered_definition(tmp_db: Connection) -> None:
    extra_field = "seed_extra"
    definition = replace(
        CODING_WORKER_TYPE_DEFINITION,
        worker_type="seed_probe",
        label="Seed probe",
        stages=(
            *CODING_WORKER_TYPE_DEFINITION.stages[:-1],
            StageDefinition(
                "needs_seed_extra",
                "Seed extra",
                extra_field,
                False,
                StageOwnershipMode.worker,
            ),
            CODING_WORKER_TYPE_DEFINITION.stages[-1],
        ),
        fields=(
            *CODING_WORKER_TYPE_DEFINITION.fields,
            FieldDefinition(extra_field, "Seed extra"),
        ),
    )
    registry = WorkerTypeRegistry(
        (definition,),
        known_skills=frozenset({"panels-worker-coding"}),
        known_toolset_profiles=frozenset({"default"}),
        employee_backend_catalog=PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.employee_backend_catalog,
    )
    previous_definitions = install_employee_runtime_definitions_for_test(
        ConfiguredEmployeeRuntimeDefinitions(
            PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.employee_backend_catalog,
            registry,
        )
    )
    try:
        seed_from_source(tmp_db, FIXTURE, worker_type="seed_probe", now=_FIXED_NOW)
    finally:
        restore_employee_runtime_definitions_for_test(previous_definitions)

    rows = {
        row["alias"]: row
        for row in tmp_db.execute("SELECT alias, worker_type, fields FROM tickets")
    }
    empty_slot = {"value": None, "proposal": None, "user_note": None}
    assert all(row["worker_type"] == "seed_probe" for row in rows.values())
    fields_by_alias = {alias: json.loads(row["fields"]) for alias, row in rows.items()}
    assert all(fields[extra_field] == empty_slot for fields in fields_by_alias.values())
    assert fields_by_alias["ticket-20260611-export-format"]["kickoff"]["value"] == (
        "- Project: Tribe"
    )
    assert fields_by_alias["ticket-20260611-export-format"]["success"]["value"] == (
        "a one-page format note that a second reader can implement from."
    )
    assert fields_by_alias["ticket-20260611-release-branch"]["approach"]["value"] == (
        "branch from main after the fixture tests pass, then tag."
    )


def test_incompatible_explicit_worker_type_rolls_back_the_whole_import(
    tmp_db: Connection,
) -> None:
    incompatible_definition = replace(
        CODING_WORKER_TYPE_DEFINITION,
        worker_type="seed_incompatible",
        label="Seed incompatible",
        stages=(
            CODING_WORKER_TYPE_DEFINITION.stages[0],
            replace(
                CODING_WORKER_TYPE_DEFINITION.stages[1],
                id="needs_seed_success",
            ),
            *CODING_WORKER_TYPE_DEFINITION.stages[2:],
        ),
    )
    registry = WorkerTypeRegistry(
        (incompatible_definition,),
        known_skills=frozenset({"panels-worker-coding"}),
        known_toolset_profiles=frozenset({"default"}),
        employee_backend_catalog=PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.employee_backend_catalog,
    )
    previous_definitions = install_employee_runtime_definitions_for_test(
        ConfiguredEmployeeRuntimeDefinitions(
            PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.employee_backend_catalog,
            registry,
        )
    )
    try:
        with pytest.raises(PlannerError) as raised:
            seed_from_source(
                tmp_db,
                FIXTURE,
                worker_type="seed_incompatible",
                now=_FIXED_NOW,
            )
    finally:
        restore_employee_runtime_definitions_for_test(previous_definitions)
    assert raised.value.code is ErrorCode.validation
    assert raised.value.message == "stage outside the linear order"
    for table in ("sprints", "sprint_items", "tickets", "ideas", "events"):
        assert _count(tmp_db, table) == 0


def test_seed_has_no_http_or_panels_command_surface() -> None:
    root = Path(__file__).resolve().parents[2]
    server_source = (root / "src/planner/core/server.py").read_text()
    cli_source = (root / "src/planner/cli/main.py").read_text()
    assert "/api/seed" not in server_source
    assert "seed" not in cli_source
