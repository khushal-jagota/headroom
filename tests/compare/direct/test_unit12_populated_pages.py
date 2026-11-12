"""Populated public page and attention proof through the existing paired hosts.

Unit 13 owns source execution, so HTTP cannot yet produce retained dataset rows.
Canonical Python writers prepare one synthetic database. The existing runner
copies it before either host writes. One paired case proves rich reads through
all six page routes, rather than substitute direct functions for HTTP wiring.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from tests.compare import runner
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.unit.test_dataset_persistence import dataset
from tests.unit.test_manager_wakes import _item_and_ticket
from tests.unit.test_measure_persistence import NOW, collect, run, source, taken

from planner.attention.history import capture_gate, record_approval
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import AgentMessageEventPayload, PromptEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    JsonDict,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect, create_schema
from planner.datasets import data as dataset_data
from planner.datasets.contracts import CandidateDatasetResult, DatasetRow
from planner.goals.actions import create_goal
from planner.measure_sources.records import encode_outputs
from planner.measures import actions as measures
from planner.measures import data as measure_data
from planner.measures import manual
from planner.measures.contracts import (
    BinaryExpression,
    CandidateObservation,
    ConstantExpression,
    CreateMeasureBody,
    ManualWriteBody,
    MeasureKind,
    MeasureOrigin,
    ReferenceExpression,
)

ROOT = Path(__file__).resolve().parents[3]
AT = int(datetime(2026, 6, 30, tzinfo=UTC).timestamp())


def page(ids: dict[str, str], title: str) -> JsonDict:
    return {
        "kind": "headroom.page",
        "version": 1,
        "title": title,
        "summary": "Populated evidence",
        "blocks": [
            {
                "type": "chart",
                "id": "manual-control",
                "kind": "line",
                "title": "Manual input",
                "measure_id": ids["manual"],
                "manual": {"model": "individual_readings"},
            },
            {
                "type": "chart",
                "id": "formula-progress",
                "kind": "line",
                "title": "Formula",
                "measure_id": ids["formula"],
                "expectation": {"op": "reference", "measure_id": ids["taken"], "day_offset": 0},
            },
            {
                "type": "chart",
                "id": "category-data",
                "kind": "bar",
                "title": "Retained dataset",
                "binding": {
                    "kind": "dataset",
                    "dataset_id": ids["dataset"],
                    "group_by": "region",
                    "filters": [],
                    "aggregation": "sum",
                    "metric": {"field": "events"},
                },
            },
            {
                "type": "table",
                "id": "retained-numbers",
                "columns": ["A", "B"],
                "rows": [["Value", "Notes"]],
                "column_widths": [50.0, 50.0],
            },
        ],
    }


async def history(db_path: str, workspace: Path, ticket: str) -> None:
    instant = [AT - 86400 * 29]
    store = ConversationStore(db_path, integer_now=lambda: instant[0])
    conversation = "unit12-populated-history"
    await store.create_conversation(
        ResolvedConversationStart(
            conversation,
            ConversationBackendKey.codex,
            "gpt-6.1-sol",
            None,
            None,
            workspace,
            ConversationAccess.full,
        )
    )
    for at, identity, recipient in (
        (AT - 86400 + 100, "replayed-owner-message", Principal(PrincipalKind.ticket, ticket)),
        (AT + 100, "replayed-owner-message", CHIEF_PRINCIPAL),
        (AT + 200, "current-owner-message", Principal(PrincipalKind.ticket, ticket)),
    ):
        instant[0] = at
        await store.append_event(
            conversation,
            PromptEventPayload(
                text_message_content("Synthetic retained owner involvement"),
                "Owner",
                PromptDeliveryMode.queue,
                sender_message_id=identity,
                sender=OWNER_PRINCIPAL,
                recipient=recipient,
            ),
        )
    instant[0] = AT + 300
    await store.append_event(
        conversation, AgentMessageEventPayload(text_message_content("Retained activity"))
    )


def seed(conn: sqlite3.Connection, workspace: Path) -> dict[str, str]:
    clock = FakeClock(datetime(2026, 6, 1, 12, tzinfo=UTC))
    manager, ticket = _item_and_ticket(conn, clock)
    provider = source(conn)
    external = taken(conn, provider)
    item = dataset(conn, provider)
    run(conn, provider)
    conn.execute(
        "UPDATE measure_source_runs SET required_outputs=? WHERE id='run'",
        (encode_outputs(measure_data.prepare_outputs(conn, provider, frozen_at=NOW)),),
    )
    conn.commit()
    collect(
        conn,
        provider,
        (
            CandidateObservation(
                external.id, "2026-10-27", 7.25, query_start_at=NOW - 86400, query_end_at=NOW
            ),
        ),
    )
    with measure_data.transaction(conn):
        dataset_data.commit_results(
            conn,
            run_id="run",
            source_id=provider,
            outputs=dataset_data.prepare_outputs(conn, provider, frozen_at=NOW),
            candidates=(
                CandidateDatasetResult(
                    item.id,
                    "2025-12-31",
                    (DatasetRow({"region": "de", "device": "web"}, {"events": -2.5}),),
                    NOW,
                ),
                CandidateDatasetResult(
                    item.id,
                    "2026-01-01",
                    (
                        DatasetRow(
                            {"region": "us", "device": "web"}, {"events": 9007199254740994.0}
                        ),
                    ),
                    NOW,
                ),
                CandidateDatasetResult(
                    item.id,
                    "2026-10-27",
                    (
                        DatasetRow({"region": "de", "device": "app"}, {"events": 3.125}),
                        DatasetRow({"region": "us", "device": "web"}, {"events": 4.75}),
                    ),
                    NOW,
                    labels={"region": {"de": "Deutschland", "us": "United States"}},
                ),
                CandidateDatasetResult(item.id, "2026-10-28", (), NOW),
            ),
            collected_at=NOW,
        )
    authored = measures.create_measure(
        conn,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Populated manual",
            unit="items",
            origin=MeasureOrigin.manual,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=None,
            formula=None,
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    for index, (day, value) in enumerate(
        (
            ("2025-12-31", -2.5),
            ("2026-01-01", 9.007199e20),
            ("2026-10-27", 3.125),
        )
    ):
        body: ManualWriteBody = {
            "request_id": f"populated-manual-{index}",
            "date": day,
            "expected_definition_revision": 1,
            "basis": "date",
            "operation": "replace",
            "expected_write_revision": index,
            "value": value,
        }
        manual.write_value(conn, authored.id, body, principal=OWNER_PRINCIPAL, now=NOW)
    formula = measures.create_measure(
        conn,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Populated formula",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=None,
            formula=BinaryExpression(
                op="multiply",
                left=ReferenceExpression(op="reference", measure_id=authored.id, day_offset=0),
                right=ConstantExpression(op="constant", value=0.5),
            ),
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    ids = {
        "manager": manager,
        "ticket": ticket,
        "manual": authored.id,
        "formula": formula.id,
        "taken": external.id,
        "dataset": item.id,
    }
    goal = create_goal(
        conn,
        {
            "project_id": "project_vylo",
            "title": "Held populated Goal",
            "kind": "binary",
            "primary_checklist_block_id": None,
            "holder_kind": "manager",
            "holder_id": manager,
            "page": page(ids, "Held Goal page"),
        },
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    ids["goal"] = goal.id
    conn.execute("UPDATE attention_history_metadata SET started_at=?", (AT - 86400 * 29,))
    capture_gate(conn, ticket_id=ticket, active=False, now=AT - 100)
    capture_gate(conn, ticket_id=ticket, active=True, now=AT + 100)
    record_approval(
        conn,
        ticket_id=ticket,
        field="brief",
        principal=OWNER_PRINCIPAL,
        method="explicit_accept",
        now=AT + 400,
    )
    capture_gate(conn, ticket_id=ticket, active=False, now=AT + 700)
    conn.commit()
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    asyncio.run(history(path, workspace, ticket))
    return ids


def requests(ids: dict[str, str]) -> tuple[list[JsonDict], list[str]]:
    manager_path = f"/api/managers/{ids['manager']}/page"
    goal_path = f"/api/goals/{ids['goal']}"
    workspace = f"/api/managers/{ids['manager']}/workspace-content"
    manual_state_key = f"/api/measures/{ids['manual']}/manual-state?date=2026-01-01"
    keys = [
        "/api/chief/page",
        workspace,
        goal_path,
        goal_path + "/readings",
        "/api/goals",
        manual_state_key,
    ]
    versions = "/api/resource-versions?keys=" + quote(json.dumps(keys), safe="")
    calls: list[JsonDict] = []
    captures: list[str] = []

    def add(
        method: str,
        path: str,
        *,
        status: int = 200,
        body: JsonDict | None = None,
        capture: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        call: JsonDict = {
            "area": "populated-pages",
            "method": method,
            "path": path,
            "status": status,
        }
        if body is not None:
            call["json"] = body
        if headers is not None:
            call["headers"] = headers
        if capture is not None:
            call["capture"] = {capture: ""}
            captures.append(capture)
        calls.append(call)

    add("POST", "/api/test/set-now", body={"now": "2026-11-04T00:30:00+05:30"})
    add("GET", versions, capture="versions_before")
    for label, key in (
        ("goal_list_before", "/api/goals"),
        ("goal_before", goal_path),
        ("goal_readings_before", goal_path + "/readings"),
        ("large_manual_state_resource", manual_state_key),
    ):
        add("GET", "/api/resource-body?key=" + quote(key, safe=""), capture=label)
    add("GET", manual_state_key, capture="large_manual_state")
    for label, path in (("chief", "/api/chief/page"), ("manager", manager_path)):
        add("GET", path, capture=label + "_empty")
        add("PATCH", path, body={"page": page(ids, label + " populated page")})
        add("GET", path, capture=label + "_page")
        add("GET", path + "/readings", capture=label + "_readings")
    add("GET", workspace, capture="held_goals")
    add("GET", f"/api/managers/{ids['manager']}/workspace", capture="workspace")
    add("GET", versions, capture="versions_after_pages")
    for label, key in (
        ("chief_resource", keys[0]),
        ("manager_resource", workspace),
        ("goal_resource", goal_path),
        ("readings_resource", goal_path + "/readings"),
        ("goal_list_resource", "/api/goals"),
    ):
        add("GET", "/api/resource-body?key=" + quote(key, safe=""), capture=label)
    invalid = page(ids, "Invalid binding")
    invalid["blocks"][1]["expectation"]["measure_id"] = "measure_absent1"
    add("PATCH", manager_path, status=404, body={"page": invalid})
    invalid = page(ids, "Invalid dataset field")
    invalid["blocks"][2]["binding"]["metric"] = {"field": "missing"}
    add("PATCH", "/api/chief/page", status=400, body={"page": invalid})
    invalid = page(ids, "Invalid manual origin")
    invalid["blocks"][0]["measure_id"] = ids["taken"]
    add("PATCH", manager_path, status=400, body={"page": invalid})
    add("GET", versions, capture="versions_after_refusals")
    add(
        "POST",
        f"/api/measures/{ids['manual']}/manual-writes",
        body={
            "request_id": "populated-manager-write",
            "date": "2026-11-03",
            "basis": "date",
            "operation": "replace",
            "expected_definition_revision": 1,
            "expected_write_revision": 3,
            "value": -7.25,
        },
        headers={"X-Plan-Actor": "manager", "X-Plan-Manager-ID": ids["manager"]},
        capture="manager_write",
    )
    add("GET", manager_path + "/readings", capture="manager_corrected")
    add("GET", "/api/chief/page/readings", capture="chief_corrected")
    add("GET", goal_path + "/readings", capture="goal_corrected")
    add("GET", versions, capture="versions_after_write")
    add(
        "GET",
        "/api/resource-body?key=" + quote(goal_path + "/readings", safe=""),
        capture="corrected_resource",
    )
    for label, key in (
        ("goal_list_corrected_resource", "/api/goals"),
        ("goal_corrected_resource", goal_path),
    ):
        add("GET", "/api/resource-body?key=" + quote(key, safe=""), capture=label)
    add(
        "POST",
        "/api/datasets/read",
        body={
            "dataset_ids": [ids["dataset"]],
            "first_date": "2025-12-31",
            "last_date": "2026-11-04",
        },
        capture="retained_dataset",
    )
    for label, suffix in (
        ("attention", "group_by=project"),
        ("attention_tickets", "group_by=ticket"),
        ("attention_manager", f"scope_kind=manager&scope_id={ids['manager']}"),
        ("attention_goal", f"scope_kind=goal&scope_id={ids['goal']}"),
    ):
        add(
            "GET",
            "/api/attention/summary?first_date=2026-06-30&last_date=2026-06-30&" + suffix,
            capture=label,
        )
    add("GET", "/api/attention/summary?first_date=bad", status=422)
    add("GET", "/api/attention/summary?group_by=bad&scope_kind=bad&limit=bad", status=422)
    for raw, status in (
        ("2026-11-02T00:00:00+99:99", 422),
        ("2026-11-02T00:00:00+01:99", 422),
        ("2026-11-02T00:00:00+24:00", 422),
        ("2026-11-02T00:00:00z", 200),
        ("0000-01-01", 422),
        ("2026-11-02T00:00:00+0100", 200),
        ("2026-11-02T00:00:00+23:59", 200),
        ("2026-11-02T00:00:00.0000001Z", 200),
        ("2026-11-02T00:00:00.000001Z", 422),
    ):
        add(
            "GET",
            "/api/attention/summary?first_date=" + quote(raw, safe="") + "&last_date=2026-11-02",
            status=status,
        )
    add(
        "POST",
        "/api/test/set-now",
        body={"now": "2027-01-01T00:30:00+14:00"},
        capture="calendar_boundary_started",
    )
    add("GET", "/api/chief/page/readings", capture="chief_year_boundary")
    add("GET", manager_path + "/readings", capture="manager_year_boundary")
    add("GET", versions, capture="versions_year_boundary")
    add(
        "GET",
        "/api/resource-body?key=" + quote(goal_path + "/readings", safe=""),
        capture="calendar_boundary_resource",
    )
    return calls, captures


def test_unit12_populated_holder_pages(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = time.perf_counter()
    (tmp_path / "fixture-home").mkdir()
    homes = create_synthetic_homes(tmp_path / "fixture-home")
    with (
        synthetic_home_environment(homes),
        closing(connect(str(tmp_path / "fixture.sqlite"))) as fixture,
    ):
        create_schema(fixture)
        ids = seed(fixture, tmp_path / "record-workspace")
        fixture.commit()
        calls, captures = requests(ids)
        view = {
            "projection_version": 7,
            "scope_generation": 1,
            "locale": "en-US",
            "selected": {"kind": "goal", "id": ids["goal"]},
            "selected_conversation_id": None,
            "review_conversation_ids": [],
            "review_skipped_keys": [],
            "review_priority_keys": [],
            "windows": [],
        }
        navigation = "/api/navigation/view/snapshot?view=" + quote(json.dumps(view), safe="")
        spec = {
            "conversation_system": "in_memory",
            "requests": calls,
            "probes": [navigation],
            "report_captures": captures,
            "source_roots": [
                "src/planner/goals/",
                "src/planner/managers/",
                "src/planner/attention/",
            ],
        }
        lists = tmp_path / "lists"
        lists.mkdir()
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        list_path = lists / "unit-12.populated-pages.json"
        list_path.write_text(json.dumps(spec))
        rules = json.loads(
            (ROOT / "tests/compare/rules/unit-12.measures-datasets.json").read_text()
        )
        rules["ignore"].pop("attention_history_metadata.started_at", None)
        rules["tokens"] = {}
        rules["response_tokens"].pop("body.receipt.payload_fingerprint", None)
        (rules_dir / list_path.name).write_text(json.dumps(rules))
        original = create_schema

        def prepared_seed(conn: sqlite3.Connection) -> None:
            original(conn)
            fixture.backup(conn)

        monkeypatch.setattr(runner, "create_schema", prepared_seed)
        phase_started: list[float] = []
        phase_finished: list[float] = []
        original_capture = runner.capture_values

        def measured_capture(body: object, call: JsonDict, saved: JsonDict) -> None:
            original_capture(body, call, saved)
            names = call.get("capture", {})
            if "calendar_boundary_started" in names:
                phase_started.append(time.perf_counter())
            if "calendar_boundary_resource" in names:
                phase_finished.append(time.perf_counter())

        monkeypatch.setattr(runner, "capture_values", measured_capture)
        right = request.config.getoption("--compare-right-module") or "tests.compare.ts_launcher"
        report = runner.run_list(list_path, tmp_path / "comparison", right)
        report["calendar_boundary_seconds"] = max(phase_finished) - min(phase_started)
    report["risk"] = (
        "Existing HTTP cannot produce retained datasets before Unit 13. This reuses the existing "
        "paired full hosts with Python canonical seed writers to prove all six public page routes, "
        "populated rich readings, Manager authority, exact retained bytes, resources and attention."
    )
    report["total_seconds"] = time.perf_counter() - started
    dbs = [tmp_path / "comparison" / name / "planning.db" for name in ("python", "candidate")]
    retained = []
    for db in dbs:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            retained.append(
                {
                    "manager": conn.execute(
                        "SELECT page FROM managers WHERE id=?", (ids["manager"],)
                    ).fetchone()[0],
                    "chief": conn.execute(
                        "SELECT page FROM chief_page WHERE singleton=1"
                    ).fetchone()[0],
                }
            )
    report["retained_page_bytes"] = retained
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / "unit12-populated-pages.json").write_text(json.dumps(report, indent=2) + "\n")
    assert not report["differences"], json.dumps(
        [
            {
                "step": item.get("step"),
                "path": item.get("call", {}).get("path"),
                "table": item.get("table"),
            }
            for item in report["differences"]
        ],
        indent=2,
    )
    assert retained[0] == retained[1]
    assert "50.0" in retained[0]["manager"]
    observed = report["captures"]
    assert observed["held_goals"]["goals"][0]["id"] == ids["goal"]
    for label in ("manager_readings", "chief_readings", "goal_corrected"):
        assert observed[label]["status_date"] == "2026-11-04"
        assert observed[label]["values"]
        assert observed[label]["charts"]["category-data"]["ranges"]
        assert observed[label]["charts"]["category-data"]["dataset_results"]
        assert any(
            result["rows"]
            for result in observed[label]["charts"]["category-data"]["dataset_results"]
        )
        assert any(
            series["points"]
            for group in observed[label]["charts"]["category-data"]["ranges"]
            for series in group["series"]
        )
        assert any(value["value"] is not None for value in observed[label]["values"])
    assert observed["retained_dataset"]["returned_rows"] == 4
    assert observed["large_manual_state"]["observation"]["value"] == 9.007199e20
    assert observed["large_manual_state_resource"]["body"] == observed["large_manual_state"]
    assert (
        next(
            value["value"]
            for value in observed["goal_readings_before"]["body"]["values"]
            if value["measure_id"] == ids["formula"] and value["date"] == "2026-01-01"
        )
        == 4.5035995e20
    )
    assert observed["manager_write"]["receipt"]["actor"]["id"] == ids["manager"]
    assert observed["attention"]["totals"]["current"]["owner_messages"]["value"] == 1
    assert observed["attention"]["totals"]["current"]["owner_approvals"]["value"] == 1
    before = {value["key"]: value["revision"] for value in observed["versions_before"]["resources"]}
    after = {
        value["key"]: value["revision"] for value in observed["versions_after_pages"]["resources"]
    }
    refused = {
        value["key"]: value["revision"]
        for value in observed["versions_after_refusals"]["resources"]
    }
    assert before["/api/chief/page"] != after["/api/chief/page"]
    assert after == refused
    for name in (
        "goal_list_before",
        "goal_before",
        "goal_readings_before",
        "large_manual_state_resource",
    ):
        assert observed[name]["revision"] == before[observed[name]["key"]]
    for name in (
        "chief_resource",
        "manager_resource",
        "goal_resource",
        "readings_resource",
        "goal_list_resource",
    ):
        assert observed[name]["revision"] == after[observed[name]["key"]]
    corrected = {
        value["key"]: value["revision"] for value in observed["versions_after_write"]["resources"]
    }
    for name in ("goal_list_corrected_resource", "goal_corrected_resource", "corrected_resource"):
        assert observed[name]["revision"] == corrected[observed[name]["key"]]
    readings_key = f"/api/goals/{ids['goal']}/readings"
    assert before[readings_key] != corrected[readings_key]
    assert observed["goal_before"]["body"] == observed["goal_corrected_resource"]["body"]
    assert observed["goal_list_before"]["body"] == observed["goal_list_corrected_resource"]["body"]
    next_refresh = int(datetime(2026, 12, 31, 23, tzinfo=UTC).timestamp())
    for name in ("chief_year_boundary", "manager_year_boundary"):
        assert observed[name]["status_date"] == "2027-01-01"
        assert observed[name]["next_refresh_at"] == next_refresh
    boundary_resource = observed["calendar_boundary_resource"]
    assert boundary_resource["body"]["status_date"] == "2027-01-01"
    assert boundary_resource["body"]["next_refresh_at"] == next_refresh
    assert boundary_resource["revision"] == next(
        item["revision"]
        for item in observed["versions_year_boundary"]["resources"]
        if item["key"] == boundary_resource["key"]
    )
    print(
        json.dumps(
            {
                "seconds": report["seconds"],
                "total_seconds": report["total_seconds"],
                "requests": report["requests"],
                "right": right,
                "calendar_boundary_seconds": report["calendar_boundary_seconds"],
            }
        )
    )
