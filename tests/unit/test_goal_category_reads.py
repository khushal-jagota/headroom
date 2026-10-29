"""Retained-read boundaries absent from the pure category arithmetic tests.

Reuse the goal and dataset fixtures. These cases protect reference validation,
root-gap selection, serialization, shared budgets, and independent refreshes.
No browser or new server fixture is necessary for these database boundaries.
"""

import json
from copy import deepcopy
from datetime import UTC, datetime
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast

import pytest
from starlette.requests import Request
from tests.unit.test_dataset_persistence import candidate, dataset
from tests.unit.test_dataset_persistence import collect as collect_dataset
from tests.unit.test_goal_pages import create, page
from tests.unit.test_measure_persistence import NOW, collect, run, source, taken

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, JsonDict, PlannerError
from planner.datasets import actions as dataset_actions
from planner.datasets.contracts import Dataset, UpdateDatasetBody
from planner.goals import configuration, data, views
from planner.goals.resource_versions import GoalResources
from planner.measures import actions as measure_actions
from planner.measures.contracts import (
    CandidateObservation,
    CreateMeasureBody,
    HistoricalUnavailableReason,
    HistoryWindow,
    MeasureKind,
    MeasureOrigin,
)


def dataset_binding(item: Dataset) -> JsonDict:
    return {
        "kind": "dataset",
        "dataset_id": item.id,
        "group_by": "region",
        "filters": [{"dimension": "device", "codes": ["web"]}],
        "aggregation": "sum",
        "metric": {"field": "events"},
    }


def all_summary(projection: Any) -> Any:
    return next(item["summary"] for item in projection["ranges"] if item["id"] == "all")


def test_dataset_binding_reads_complete_retained_maps_and_validates_current_references(
    tmp_db: Connection,
) -> None:
    item = dataset(tmp_db, source(tmp_db))
    run(tmp_db, item.source_id)
    collect_dataset(
        tmp_db,
        item,
        candidate(item, "2026-10-25", "de"),
        candidate(item, "2026-10-27", "us"),
    )
    chart = {"type": "chart", "kind": "bar", "binding": dataset_binding(item), "target": 1}
    goal = create(tmp_db, kind="quantitative", primary_chart=chart)
    assert goal.primary_chart is not None
    result = views.readings_json(
        tmp_db,
        goal.id,
        datetime.fromtimestamp(NOW, UTC),
        first_date="2026-10-25",
        last_date="2026-10-27",
    )
    projection = result["charts"][goal.primary_chart["id"]]
    assert projection["kind"] == "category" and result["values"] == []
    summary = all_summary(projection)
    assert summary["actual"]["value"] == 4
    assert summary["actual"]["partial"] and summary["actual"]["missing_dates"] == ["2026-10-26"]
    assert result["status"] == "neutral"
    assert projection["dataset_results"][0]["output"]["fields"][0]["name"] == "events"
    assert projection["dataset_results"][0]["provenance"]["script_sha256"] == "synthetic"
    assert {value["source_id"] for value in result["sources"]} == {item.source_id}
    json.dumps(result, allow_nan=False)

    for invalid in (
        {**chart, "measure_id": "conflict"},
        {**chart, "series": []},
        {**chart, "binding": {**dataset_binding(item), "group_by": "absent"}},
        {**chart, "binding": {**dataset_binding(item), "metric": {"field": "absent"}}},
    ):
        with pytest.raises(PlannerError):
            create(tmp_db, primary_chart=invalid)


def test_live_time_budget_uses_latest_submitted_date_including_explicit_gap(
    tmp_db: Connection,
) -> None:
    identifier = source(tmp_db)
    spent, planned = taken(tmp_db, identifier), taken(tmp_db, identifier)
    run(tmp_db, identifier)
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(spent.id, "2026-10-26", 2),
            CandidateObservation(planned.id, "2026-10-26", 8),
            CandidateObservation(spent.id, "2026-10-27", 3),
        ),
    )
    goal = create(
        tmp_db,
        kind="quantitative",
        primary_chart={
            "type": "chart",
            "kind": "time-budget",
            "binding": {
                "kind": "scalar_inputs",
                "aggregation": "latest",
                "inputs": [
                    {"key": "spent", "label": "Spent", "measure_id": spent.id, "role": "spent"},
                    {
                        "key": "planned",
                        "label": "Planned",
                        "measure_id": planned.id,
                        "role": "planned",
                    },
                ],
            },
        },
    )
    assert goal.primary_chart is not None and "target" not in goal.primary_chart
    now = datetime.fromtimestamp(NOW, UTC)
    reading = views.readings_json(
        tmp_db, goal.id, now, first_date="2026-10-26", last_date="2026-10-28"
    )
    summary = all_summary(reading["charts"][goal.primary_chart["id"]])
    assert summary["date"] == "2026-10-27"
    assert summary["comparison"]["value"] is None and summary["meets_expectation"] is None
    assert reading["status"] == "neutral"
    assert any(
        value["measure_id"] == spent.id and value["value"] == 3 for value in reading["values"]
    )
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(
                planned.id, "2026-10-28", None, HistoricalUnavailableReason.provider_has_no_value
            ),
        ),
        history=HistoryWindow("2026-10-26", "2026-10-28"),
    )
    unavailable = views.readings_json(
        tmp_db, goal.id, now, first_date="2026-10-26", last_date="2026-10-29"
    )
    assert all_summary(unavailable["charts"][goal.primary_chart["id"]])["date"] == "2026-10-28"
    with pytest.raises(PlannerError, match="positive target"):
        create(tmp_db, primary_chart={"type": "chart", "kind": "time-budget"})


def test_category_changes_and_dataset_midnight_refresh_readings_without_document_changes(
    tmp_db: Connection,
) -> None:
    item = dataset(tmp_db, source(tmp_db))
    dataset_actions.update_dataset(
        tmp_db,
        item.id,
        UpdateDatasetBody(expected_revision=1, timezone="Pacific/Auckland"),
        principal=CHIEF_PRINCIPAL,
        now=NOW,
    )
    run(tmp_db, item.source_id)
    collect_dataset(tmp_db, item, candidate(item, "2026-10-25", "de"))
    goal = create(
        tmp_db,
        primary_chart={
            "type": "chart",
            "kind": "bar",
            "binding": dataset_binding(item),
        },
    )
    draft = deepcopy(goal.page)
    draft["summary"] = "Unsaved local work"
    clock = FakeClock(datetime(2026, 10, 27, 10, 30, tzinfo=UTC))
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(clock=clock))})
    provider = GoalResources()
    detail = provider.capture(tmp_db, request, f"/api/goals/{goal.id}")
    initial = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    assert detail is not None and initial is not None
    collect_dataset(tmp_db, item, candidate(item, "2026-10-25", "de", "us"))
    corrected = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    unchanged_detail = provider.capture(tmp_db, request, f"/api/goals/{goal.id}")
    assert corrected is not None and corrected.revision != initial.revision
    assert unchanged_detail is not None and unchanged_detail.revision == detail.revision
    assert (
        data.read_goal(tmp_db, goal.id).page == goal.page
        and draft["summary"] == "Unsaved local work"
    )
    body = cast(JsonDict, corrected.read())
    assert body["next_refresh_at"] == int(datetime(2026, 10, 27, 11, tzinfo=UTC).timestamp())
    clock.set(datetime(2026, 10, 27, 11, 30, tzinfo=UTC))
    midnight = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    assert midnight is not None and midnight.revision != corrected.revision


def test_named_input_demands_share_the_formula_budget_and_deduplicate_legacy_values(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = source(tmp_db)
    actual, category = taken(tmp_db, identifier), taken(tmp_db, identifier)
    document = page()
    document["blocks"].append(
        {
            "type": "chart",
            "id": "composition",
            "kind": "bar",
            "binding": {
                "kind": "scalar_inputs",
                "aggregation": "sum",
                "inputs": [
                    {"key": "shared", "label": "Shared", "measure_id": actual.id},
                    {"key": "other", "label": "Other", "measure_id": category.id},
                ],
            },
        }
    )
    goal = create(
        tmp_db,
        page=document,
        primary_chart={
            "type": "chart",
            "kind": "number",
            "measure_id": actual.id,
            "expectation": {"op": "constant", "value": 1},
        },
    )
    now = datetime.fromtimestamp(NOW, UTC)
    monkeypatch.setattr(configuration, "MAX_DEMANDED_MEASURE_DATES", 3)
    with pytest.raises(PlannerError, match="3.*narrower"):
        views.readings_json(tmp_db, goal.id, now, first_date="2026-10-26", last_date="2026-10-27")
    monkeypatch.setattr(configuration, "MAX_DEMANDED_MEASURE_DATES", 4)
    result = views.readings_json(
        tmp_db, goal.id, now, first_date="2026-10-26", last_date="2026-10-27"
    )
    identities = [(value["measure_id"], value["date"]) for value in result["values"]]
    assert len(identities) == len(set(identities)) == 4
    monkeypatch.setattr(configuration, "MAX_PROJECTED_CHART_DATES", 3)
    with pytest.raises(PlannerError, match="3.*narrower"):
        views.readings_json(tmp_db, goal.id, now, first_date="2026-10-26", last_date="2026-10-27")


def test_named_assembled_inputs_do_not_create_future_actuals(tmp_db: Connection) -> None:
    measure = measure_actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Constant",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=None,
            formula={"op": "constant", "value": 7},
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    goal = create(
        tmp_db,
        primary_chart={
            "type": "chart",
            "kind": "allocation",
            "binding": {
                "kind": "scalar_inputs",
                "aggregation": "latest",
                "inputs": [
                    {"key": "constant", "label": "Constant", "measure_id": measure.id},
                ],
            },
        },
    )
    assert goal.primary_chart is not None
    result = views.readings_json(
        tmp_db,
        goal.id,
        datetime.fromtimestamp(NOW, UTC),
        first_date="2026-10-26",
        last_date="2026-10-28",
    )
    summary = all_summary(result["charts"][goal.primary_chart["id"]])
    assert summary["date"] == "2026-10-27" and summary["actual"]["value"] == 7
    future = next(value for value in result["values"] if value["date"] == "2026-10-28")
    assert future["value"] is None
