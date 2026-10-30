"""New boundary risks: document races, independent state, and live measure projections."""

import json
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from starlette.requests import Request
from tests.unit.test_manual_measures import manual_measure
from tests.unit.test_measure_persistence import NOW, collect, run, source, taken

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL, JsonDict, PlannerError, Principal, PrincipalKind
from planner.goals import actions, data, views
from planner.goals.contracts import ChartBlock, ChecklistBlock, Goal, GoalsResponse, SegmentedBlock
from planner.goals.logic.status import (
    calendar_projection,
    chart_target,
    goal_status,
    quantitative_status,
)
from planner.goals.resource_versions import GoalResources
from planner.measures import actions as measure_actions
from planner.measures.contracts import (
    CandidateObservation,
    ConstantExpression,
    CountPeriod,
    CreateMeasureBody,
    MeasureKind,
    MeasureOrigin,
    MeasureReadStatus,
    MeasureValue,
    ScalarCoverageStatus,
)


def page() -> JsonDict:
    return {
        "kind": "headroom.page",
        "version": 1,
        "title": "Approach",
        "summary": "",
        "blocks": [
            {"type": "text", "text": "Start with the saved plan."},
            {"type": "checklist", "items": [{"label": "Write the draft", "state": "open"}]},
        ],
    }


def create(conn: Connection, **extra: Any) -> Goal:
    return actions.create_goal(
        conn,
        {"project_id": "project_vylo", "title": "Ship the guide", "page": page(), **extra},
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )


def test_saved_page_race_and_independent_completion_activation(tmp_db: Connection) -> None:
    goal = create(tmp_db, deadline="2026-10-28")
    agent = Principal(PrincipalKind.goal, goal.id)
    draft = deepcopy(goal.page)
    draft["blocks"].reverse()
    cast(ChecklistBlock, draft["blocks"][0])["items"][0]["state"] = "done"
    accepted = actions.update_goal(
        tmp_db,
        goal.id,
        {"expected_revision": 1, "page": draft, "completed": True},
        principal=agent,
        now=NOW + 1,
    )
    assert accepted.active and accepted.binary_checked
    assert data.read_goal(tmp_db, goal.id).page == draft
    assert [item.id for item in data.list_goals(tmp_db, active=True)] == [goal.id]
    with pytest.raises(PlannerError, match="goal changed"):
        actions.update_goal(
            tmp_db,
            goal.id,
            {"expected_revision": 1, "page": goal.page},
            principal=OWNER_PRINCIPAL,
            now=NOW + 2,
        )
    inactive = actions.update_goal(
        tmp_db, goal.id, {"expected_revision": 2, "active": False}, principal=agent, now=NOW + 3
    )
    assert inactive.binary_checked and not data.list_goals(tmp_db, active=True)
    reactivated = actions.update_goal(
        tmp_db, goal.id, {"expected_revision": 3, "active": True}, principal=agent, now=NOW + 4
    )
    assert reactivated.active and reactivated.binary_checked
    reversed_goal = actions.update_goal(
        tmp_db, goal.id, {"expected_revision": 4, "completed": False}, principal=agent, now=NOW + 5
    )
    assert reversed_goal.active and not reversed_goal.binary_checked
    other = create(tmp_db, title="Another goal")
    with pytest.raises(PlannerError):
        actions.update_goal(
            tmp_db,
            other.id,
            {"expected_revision": 1, "active": False},
            principal=agent,
            now=NOW + 6,
        )


@pytest.mark.parametrize("kind", [MeasureKind.reading, MeasureKind.running_total])
def test_projection_keeps_signed_gaps_and_refresh_does_not_change_document(
    tmp_db: Connection,
    kind: MeasureKind,
) -> None:
    source_id = source(tmp_db)
    measure = taken(tmp_db, source_id, kind=kind)
    run(tmp_db, source_id)
    collect(
        tmp_db,
        source_id,
        (
            CandidateObservation(measure.id, "2026-10-25", -2.5),
            CandidateObservation(measure.id, "2026-10-27", -1.25),
        ),
    )
    goal = create(
        tmp_db,
        kind="quantitative",
        primary_chart={
            "type": "chart",
            "kind": "line",
            "measure_id": measure.id,
            "target": -1.0,
            "direction": "atleast",
            "domain": [-5, 5],
        },
    )
    clock = FakeClock(datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin")))
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(clock=clock))})
    provider = GoalResources()
    detail = provider.capture(tmp_db, request, f"/api/goals/{goal.id}")
    first = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    assert detail and first
    result = cast(JsonDict, first.read())
    assert [value["value"] for value in result["values"]] == [-2.5, None, -1.25]
    assert result["values"][1]["status"] == "missing_input"
    assert result["status"] == "bad"
    reference: ChartBlock = {
        "type": "chart",
        "id": "reference",
        "kind": "line",
        "placement": "primary",
        "target": 10.0,
        "reference": [{"date": "2026-10-25", "value": -3.0}, {"date": "2026-10-29", "value": 1.0}],
    }
    assert chart_target(reference, "2026-10-27") == -1.0
    assert chart_target(reference, "2026-10-30") is None
    assert quantitative_status(-1.25, chart_target(reference, "2026-10-27"), "atmost") == "good"
    collect(tmp_db, source_id, (CandidateObservation(measure.id, "2026-10-27", -0.5),))
    refreshed = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    unchanged = provider.capture(tmp_db, request, f"/api/goals/{goal.id}")
    assert refreshed and unchanged
    assert refreshed.revision != first.revision
    assert unchanged.revision == detail.revision
    assert data.read_goal(tmp_db, goal.id).page == goal.page
    assert cast(JsonDict, refreshed.read())["values"][-1]["value"] == -0.5
    assert cast(JsonDict, refreshed.read())["status"] == "good"
    clock.set(datetime(2026, 10, 28, 0, 5, tzinfo=ZoneInfo("Europe/Berlin")))
    missing = views.readings_json(tmp_db, goal.id, clock.now())
    assert missing["values"][-1]["value"] is None
    assert missing["status"] == "neutral"  # never carry yesterday's reading into today


def test_binary_midnight_revision_and_dst_calendar(tmp_db: Connection) -> None:
    goal = create(tmp_db, deadline="2026-10-28")
    clock = FakeClock(datetime(2026, 10, 27, 23, 59, tzinfo=ZoneInfo("Europe/Berlin")))
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(clock=clock))})
    provider = GoalResources()
    before = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    assert before and cast(JsonDict, before.read())["status"] == "neutral"
    clock.set(datetime(2026, 10, 28, 0, 0, tzinfo=ZoneInfo("Europe/Berlin")))
    due = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    assert (
        due
        and due.revision != before.revision
        and cast(JsonDict, due.read())["status"] == "attention"
    )
    assert goal_status(False, "2026-10-28", "2026-10-29") == "bad"
    assert goal_status(True, "2026-10-28", "2026-10-29") == "good"
    before_dst = datetime(2026, 10, 25, 0, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert calendar_projection(before_dst)[1] - int(before_dst.timestamp()) == 25 * 3600


def test_document_rejects_bad_ids_and_preserves_static_signed_charts(tmp_db: Connection) -> None:
    document = page()
    document["blocks"].append(
        {
            "type": "chart",
            "kind": "bar",
            "series": [
                {
                    "name": "Correction",
                    "points": [{"label": "Before", "value": -2.5}, {"label": "Gap", "value": None}],
                }
            ],
        }
    )
    goal = create(tmp_db, page=document, primary_chart={"type": "chart", "kind": "line"})
    assert goal.primary_chart is not None
    assert goal.primary_chart["id"] and goal.primary_chart["placement"] == "primary"
    assert all(block["id"] for block in goal.page["blocks"])
    assert cast(ChecklistBlock, goal.page["blocks"][1])["items"][0]["id"]
    assert cast(ChartBlock, goal.page["blocks"][-1])["placement"] == "supporting"
    accepted = deepcopy(goal.page)
    accepted["blocks"][1]["id"] = accepted["blocks"][0]["id"]
    with pytest.raises(PlannerError, match="unique"):
        actions.update_goal(
            tmp_db,
            goal.id,
            {"expected_revision": 1, "page": accepted},
            principal=OWNER_PRINCIPAL,
            now=NOW + 1,
        )
    assert data.read_goal(tmp_db, goal.id).revision == 1
    segmented = deepcopy(document)
    segmented["blocks"].append(
        {"type": "segmented", "views": [{"label": "Overview", "blocks": []}]}
    )
    with pytest.raises(PlannerError, match=r"\$\.blocks\[3\]\.views\[0\]\.id: required"):
        create(tmp_db, page=segmented)
    segmented["blocks"][-1]["views"][0]["id"] = "overview"
    accepted_segmented = create(tmp_db, page=segmented)
    saved_segmented = cast(SegmentedBlock, accepted_segmented.page["blocks"][-1])
    assert saved_segmented["views"][0]["id"] == "overview"


def test_goal_list_revision_tracks_conversation_links_without_document_edits(
    tmp_db: Connection,
) -> None:
    goal = create(tmp_db)
    clock = FakeClock(datetime(2026, 10, 28, 12, tzinfo=ZoneInfo("Europe/Berlin")))
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(clock=clock))})
    provider = GoalResources()
    before = provider.capture(tmp_db, request, "/api/goals")
    assert before and cast(GoalsResponse, before.read())["goals"][0]["conversation_id"] is None
    tmp_db.execute(
        "INSERT INTO agents(agent_key,conversation_id) VALUES(?,?)",
        ("goal_" + goal.id, "goal-list-conversation"),
    )
    tmp_db.commit()
    linked = provider.capture(tmp_db, request, "/api/goals")
    assert linked and linked.revision != before.revision
    assert (
        cast(GoalsResponse, linked.read())["goals"][0]["conversation_id"]
        == "goal-list-conversation"
    )
    tmp_db.execute("UPDATE agents SET conversation_id=NULL WHERE agent_key=?", ("goal_" + goal.id,))
    tmp_db.commit()
    reset = provider.capture(tmp_db, request, "/api/goals")
    assert reset and reset.revision != linked.revision
    assert cast(GoalsResponse, reset.read())["goals"][0]["conversation_id"] is None
    assert data.read_goal(tmp_db, goal.id).revision == 1


def test_sheet_charts_preserve_summary_semantics_and_saved_cell_tones(tmp_db: Connection) -> None:
    """Saved options and default summaries must agree across API and production renderer."""
    from planner.goals.logic.status import chart_status, chart_summary

    chart: ChartBlock = {
        "type": "chart",
        "id": "summary",
        "kind": "line",
        "placement": "primary",
        "aggregate": "sum",
        "target": 3,
        "below_target_tone": "attention",
        "target_label": "floor",
        "difference_unit": "pp",
        "plot_height": 260,
        "emphasis": "hero",
        "scale_label": "12 interviews",
        "shortfall_label": "to target",
        "within_limit_label": "remaining",
        "series": [
            {
                "name": "Teams",
                "points": [
                    {"label": "First", "value": 2},
                    {"label": "Second", "value": None},
                    {"label": "Third", "value": 4},
                ],
            }
        ],
    }
    assert chart_summary(chart, []) == (6, 9)
    assert chart_status(chart, []) == "attention"
    empty = {**chart, "series": [{"name": "Teams", "points": [{"label": "First", "value": None}]}]}
    assert chart_summary(cast(ChartBlock, empty), []) == (None, 3)
    assert chart_status(cast(ChartBlock, empty), []) == "neutral"
    chart["ranges"] = [
        {
            "id": "all",
            "label": "All time",
            "series": [
                {"name": "Teams", "points": [{"label": "All", "value": 10}]},
            ],
        }
    ]
    assert chart_summary(chart, []) == (10, 3)  # all range overrides the static fallback
    chart["ranges"][0]["target"] = 12
    assert chart_summary(chart, []) == (10, 12)
    assert chart_status(chart, []) == "attention"
    chart["ranges"][0]["id"] = "week"
    chart["ranges"][0]["target"] = 2
    assert chart_summary(chart, []) == (6, 9)  # base series wins when all is not authored
    without_base = cast(ChartBlock, {key: value for key, value in chart.items() if key != "series"})
    assert chart_summary(without_base, []) == (10, 2)  # fallback range carries its own target
    del chart["ranges"]
    chart["domain"] = (0, 15)
    document = page()
    document["blocks"].append(
        {
            "type": "table",
            "columns": ["Experiment", "Outcome"],
            "rows": [["First", "Keep"], ["Second", "Stop"]],
            "cell_tones": [[None, "good"], [None, "info"]],
            "column_widths": [60, 40],
            "empty_message": "No experiments recorded",
        }
    )
    goal = create(tmp_db, kind="quantitative", primary_chart=chart, page=document)
    assert goal.primary_chart == chart
    saved = data.read_goal(tmp_db, goal.id)
    assert saved.page["blocks"][-1] == goal.page["blocks"][-1]
    now = datetime(2026, 10, 28, 12, tzinfo=ZoneInfo("Europe/Berlin"))
    assert views.readings_json(tmp_db, goal.id, now)["status"] == "attention"
    invalid = deepcopy(goal.page)
    cast(Any, invalid["blocks"][-1])["cell_tones"] = [["good"]]
    with pytest.raises(PlannerError, match="cell tones"):
        actions.update_goal(
            tmp_db,
            goal.id,
            {"expected_revision": 1, "page": invalid},
            principal=OWNER_PRINCIPAL,
            now=NOW + 1,
        )
    assert data.read_goal(tmp_db, goal.id).revision == 1
    for widths in ([60], [0, 100], [60, 60]):
        invalid = deepcopy(goal.page)
        cast(Any, invalid["blocks"][-1])["column_widths"] = widths
        with pytest.raises(PlannerError, match="column widths"):
            actions.update_goal(
                tmp_db,
                goal.id,
                {"expected_revision": 1, "page": invalid},
                principal=OWNER_PRINCIPAL,
                now=NOW + 1,
            )


def test_sheet_category_totals_share_one_identity_and_meter_denominators() -> None:
    """A missing category cannot become a false total; meter fractions need a denominator."""
    from planner.goals.logic.documents import validate_chart
    from planner.goals.logic.status import chart_status, chart_summary

    chart: ChartBlock = {
        "type": "chart",
        "id": "categories",
        "kind": "allocation",
        "placement": "supporting",
        "allocation_mode": "percentage",
        "total_label": "Planned",
        "series": [
            {
                "name": "Research",
                "color": "blue",
                "points": [{"label": "Monday", "value": 3}, {"label": "Tuesday", "value": 4}],
            },
            {"name": "Build", "color": "blue-muted", "points": [{"label": "Monday", "value": 4}]},
            {
                "name": "Review",
                "color": "blue-dark",
                "points": [{"label": "Monday", "value": 1}, {"label": "Tuesday", "value": 2}],
            },
        ],
    }
    validate_chart(chart)
    assert chart_summary(chart, []) == (None, None)
    chart["series"][1]["points"].append({"label": "Tuesday", "value": 3})
    assert chart_summary(chart, []) == (9, None)
    assert chart_status(chart, []) == "neutral"
    chart.update({"kind": "bar", "stacked": True, "target": 10})
    assert chart_summary(chart, []) == (9, 10)
    assert chart_status(chart, []) == "bad"
    chart = {
        "type": "chart",
        "id": "budget",
        "kind": "time-budget",
        "placement": "primary",
        "target": 8,
        "measure_id": "existing",
    }
    validate_chart(chart)
    assert chart_status(chart, [("2026-10-28", 3)]) == "good"
    assert chart_status(chart, [("2026-10-28", 10)]) == "bad"
    assert chart_summary(chart, [("2026-10-28", -1)]) == (None, 8)
    assert chart_status(chart, [("2026-10-28", -1)]) == "neutral"
    chart.update({"kind": "progress", "target": 12})
    assert chart_status(chart, [("2026-10-28", 14)]) == "good"
    assert chart_status(chart, [("2026-10-28", -1)]) == "neutral"
    chart["target"] = 0
    with pytest.raises(ValueError, match="positive target"):
        validate_chart(chart)
    chart["target"] = 12
    chart["series"] = [{"name": "Completed", "points": [{"label": "Today", "value": -1}]}]
    with pytest.raises(ValueError, match="nonnegative"):
        validate_chart(chart)
    chart["kind"] = "bar"
    validate_chart(chart)  # prior signed line/bar contract is unchanged


def test_manual_controls_require_matching_models_and_manual_origin(tmp_db: Connection) -> None:
    """A saved control must edit its explicit manual measure, never provider or formula data."""
    from planner.goals.logic.documents import validate_chart

    measure = manual_measure(tmp_db)
    daily: ChartBlock = {
        "type": "chart",
        "id": "daily",
        "kind": "bar",
        "placement": "primary",
        "measure_id": measure.id,
        "target": 8,
        "manual": {"model": "daily_quota", "increments": [-0.5, 1]},
    }
    cumulative: ChartBlock = {
        "type": "chart",
        "id": "cumulative",
        "kind": "gauge",
        "placement": "supporting",
        "measure_id": measure.id,
        "target": 20,
        "manual": {"model": "cumulative_target", "increments": [2]},
    }
    reading: ChartBlock = {
        "type": "chart",
        "id": "reading",
        "kind": "line",
        "placement": "supporting",
        "measure_id": measure.id,
        "manual": {"model": "individual_readings"},
    }
    document = page()
    document["blocks"].extend([cumulative, reading])
    goal = create(tmp_db, kind="quantitative", primary_chart=daily, page=document)
    saved = data.read_goal(tmp_db, goal.id)
    assert saved.primary_chart == daily and saved.page == goal.page

    external = taken(tmp_db, source(tmp_db))
    assembled = measure_actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="Derived",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="UTC",
            period=None,
            source_id=None,
            formula=ConstantExpression(op="constant", value=2),
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    with pytest.raises(PlannerError, match="manual-origin"):
        create(tmp_db, kind="quantitative", primary_chart={**daily, "measure_id": external.id})
    rejected = deepcopy(saved.page)
    cast(ChartBlock, rejected["blocks"][-1])["measure_id"] = assembled.id
    with pytest.raises(PlannerError, match="manual-origin"):
        actions.update_goal(
            tmp_db,
            goal.id,
            {"expected_revision": 1, "page": rejected},
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
    assert data.read_goal(tmp_db, goal.id).page == saved.page

    for invalid, message in (
        ({**daily, "kind": "line"}, "matching chart kind"),
        ({key: value for key, value in daily.items() if key != "measure_id"}, "measure binding"),
        ({key: value for key, value in daily.items() if key != "target"}, "require a target"),
        ({**cumulative, "target": 0}, "positive target"),
    ):
        with pytest.raises(ValueError, match=message):
            validate_chart(cast(ChartBlock, invalid))


def test_expectation_save_uses_formula_parser_and_validates_all_static_inputs(
    tmp_db: Connection,
) -> None:
    """JSON periods bypass generic enum checks; inactive references still need save validation."""
    identifier = source(tmp_db)
    measure = taken(tmp_db, identifier)
    run(tmp_db, identifier)
    collect(
        tmp_db,
        identifier,
        tuple(
            CandidateObservation(measure.id, day, value)
            for day, value in (
                ("2026-10-01", 50),
                ("2026-10-26", 2),
                ("2026-10-27", 3),
                ("2026-10-28", 4),
                ("2026-10-29", 5),
                ("2026-10-30", 6),
                ("2026-10-31", 7),
                ("2026-11-01", 8),
            )
        ),
    )
    chart = {
        "type": "chart",
        "kind": "number",
        "measure_id": measure.id,
        "expectation": {"op": "sum_daily", "measure_id": measure.id, "period": "week"},
    }
    goal = create(tmp_db, kind="quantitative", primary_chart=chart)
    assert data.read_goal(tmp_db, goal.id).primary_chart == goal.primary_chart
    assert goal.primary_chart is not None
    now = datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin"))
    weekly = views.readings_json(
        tmp_db, goal.id, now, first_date="2026-10-27", last_date="2026-10-27"
    )
    assert weekly["charts"][goal.primary_chart["id"]]["points"][0]["expected"]["value"] == 35
    document = page()
    document["blocks"].append(
        {
            **chart,
            "id": "daily",
            "expectation": {"op": "sum_daily", "measure_id": measure.id, "period": "day"},
        }
    )
    nested = create(tmp_db, page=document)
    assert nested.page["blocks"][-1]["type"] == "chart"
    daily = views.readings_json(
        tmp_db, nested.id, now, first_date="2026-10-27", last_date="2026-10-27"
    )
    assert daily["charts"]["daily"]["points"][0]["expected"]["value"] == 3
    invalid = {
        **chart,
        "expectation": {
            "op": "if_before",
            "date": "2026-10-28",
            "before": {"op": "constant", "value": 1},
            "otherwise": {"op": "reference", "measure_id": "missing", "day_offset": 0},
        },
    }
    with pytest.raises(PlannerError):
        create(tmp_db, primary_chart=invalid)
    for changed in (
        {"kind": "bar"},
        {"stacked": True},
        {"aggregate": "sum"},
        {"manual": {"model": "individual_readings"}, "kind": "line"},
        {"measure_id": ""},
    ):
        with pytest.raises(PlannerError, match="expectations require"):
            create(tmp_db, primary_chart={**chart, **changed})
    with pytest.raises(PlannerError, match="expression"):
        create(tmp_db, primary_chart={**chart, "expectation": {"op": "calendar_month_growth"}})
    with pytest.raises(PlannerError, match="date"):
        create(
            tmp_db,
            primary_chart={**chart, "expectation": {"op": "elapsed_days", "start": "2026-02-30"}},
        )


def test_formula_projection_uses_same_date_and_keeps_future_path_out_of_summary(
    tmp_db: Connection,
) -> None:
    """Deadline samples must not replace today's actual or use a legacy target fallback."""
    identifier = source(tmp_db)
    measure = taken(tmp_db, identifier)
    run(tmp_db, identifier)
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(measure.id, "2026-10-25", 150),
            CandidateObservation(measure.id, "2026-10-27", 150),
        ),
    )
    goal = create(
        tmp_db,
        kind="quantitative",
        deadline="2026-10-31",
        primary_chart={
            "type": "chart",
            "kind": "line",
            "measure_id": measure.id,
            "target": 1,
            "expectation": {
                "op": "if_before",
                "date": "2026-10-28",
                "before": {
                    "op": "add",
                    "left": {"op": "constant", "value": 100},
                    "right": {
                        "op": "multiply",
                        "left": {"op": "constant", "value": 2},
                        "right": {"op": "elapsed_days", "start": "2026-10-01"},
                    },
                },
                "otherwise": {"op": "constant", "value": 180},
            },
        },
    )
    assert goal.primary_chart is not None
    now = datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin"))
    result = views.readings_json(tmp_db, goal.id, now)
    projection = result["charts"][goal.primary_chart["id"]]
    assert (projection["first_date"], projection["last_date"], projection["summary_date"]) == (
        "2026-09-28",
        "2026-10-31",
        "2026-10-27",
    )
    samples = {point["date"]: point for point in projection["points"]}
    assert (samples["2026-10-25"]["difference"], samples["2026-10-27"]["difference"]) == (2, -2)
    assert samples["2026-10-25"]["meets_expectation"] is True
    assert samples["2026-10-27"]["meets_expectation"] is False
    assert samples["2026-10-28"]["break_before"] is True
    assert samples["2026-10-28"]["expected"]["value"] == 180
    assert samples["2026-10-28"]["actual"]["value"] is None
    assert samples["2026-10-28"]["meets_expectation"] is None
    assert result["status"] == "bad"
    assert [value["date"] for value in result["values"]] == [
        "2026-10-25",
        "2026-10-26",
        "2026-10-27",
    ]
    json.dumps(result, allow_nan=False)
    future = views.readings_json(
        tmp_db, goal.id, now, first_date="2026-11-01", last_date="2026-11-02"
    )
    assert future["charts"][goal.primary_chart["id"]]["summary_date"] is None
    assert future["status"] == "neutral"
    assert future["charts"][goal.primary_chart["id"]]["points"][-1]["expected"]["value"] == 180
    tomorrow = views.readings_json(tmp_db, goal.id, now.replace(day=28))
    assert tomorrow["status"] == "neutral"
    assert data.read_goal(tmp_db, goal.id).binary_checked is False


def test_formula_inputs_follow_corrections_and_deleted_inactive_inputs(
    tmp_db: Connection,
) -> None:
    """Expectation-only dependencies need revision/provenance without becoming actual series."""
    identifier = source(tmp_db)
    actual = taken(tmp_db, identifier)
    baseline = taken(tmp_db, identifier)
    optional = manual_measure(tmp_db, zone="Pacific/Auckland")
    run(tmp_db, identifier)
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(actual.id, "2026-10-27", 120),
            CandidateObservation(baseline.id, "2026-10-01", 100),
        ),
    )
    goal = create(
        tmp_db,
        kind="quantitative",
        primary_chart={
            "type": "chart",
            "kind": "number",
            "measure_id": actual.id,
            "expectation": {
                "op": "if_before",
                "date": "2026-10-28",
                "before": {"op": "reference_at", "measure_id": baseline.id, "date": "2026-10-01"},
                "otherwise": {"op": "reference", "measure_id": optional.id, "day_offset": 0},
            },
        },
    )
    assert goal.primary_chart is not None
    chart_id = goal.primary_chart["id"]
    clock = FakeClock(datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin")))
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(clock=clock))})
    provider = GoalResources()
    first = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    assert first is not None
    reading = cast(JsonDict, first.read())
    assert {value["measure_id"] for value in reading["values"]} == {actual.id}
    point = reading["charts"][chart_id]["points"][-1]
    assert point["difference"] == 20
    assert point["expected"]["inputs"][0]["measure_id"] == baseline.id
    assert point["expected"]["inputs"][0]["run_id"] == "run"
    collect(tmp_db, identifier, (CandidateObservation(baseline.id, "2026-10-01", 130),))
    corrected = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    assert corrected is not None and corrected.revision != first.revision
    assert cast(JsonDict, corrected.read())["charts"][chart_id]["points"][-1]["difference"] == -10
    measure_actions.delete_measure(tmp_db, optional.id, principal=OWNER_PRINCIPAL, now=NOW)
    deleted = provider.capture(tmp_db, request, f"/api/goals/{goal.id}/readings")
    assert deleted is not None
    assert (
        cast(JsonDict, deleted.read())["charts"][chart_id]["points"][-1]["expected"]["value"] == 130
    )
    inactive = views.readings_json(
        tmp_db, goal.id, clock.now(), first_date="2026-10-28", last_date="2026-10-28"
    )
    expected = inactive["charts"][chart_id]["points"][0]["expected"]
    assert expected["status"] == "missing_input" and expected["value"] is None
    assert expected["missing_inputs"] == ({"measure_id": optional.id, "date": "2026-10-28"},)
    assert inactive["status"] == "neutral"


def test_formula_projection_limits_reject_before_date_allocation(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized requests must fail without allocating intervals or returning truncated success."""
    measure = manual_measure(tmp_db)
    chart = {
        "type": "chart",
        "kind": "line",
        "measure_id": measure.id,
        "expectation": {"op": "constant", "value": 100},
    }
    goal = create(tmp_db, primary_chart=chart)
    now = datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin"))

    def forbidden(*args: Any) -> Any:
        pytest.fail("oversized interval was allocated")

    monkeypatch.setattr(views, "date_range", forbidden)
    with pytest.raises(PlannerError, match="3660.*narrower"):
        views.readings_json(tmp_db, goal.id, now, first_date="2000-01-01", last_date="2026-10-27")
    document = page()
    document["blocks"].extend(deepcopy(chart) for _ in range(14))
    many = create(tmp_db, page=document)
    with pytest.raises(PlannerError, match="50000.*narrower"):
        views.readings_json(tmp_db, many.id, now, first_date="2020-01-01", last_date="2029-12-31")


def test_goal_demand_limit_includes_expanded_measure_inputs(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A small visible interval can still demand too many distinct dependency dates."""
    from planner.goals import configuration

    actual = manual_measure(tmp_db)
    derived = measure_actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="prior",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="Europe/Berlin",
            period=None,
            source_id=None,
            formula={"op": "reference", "measure_id": actual.id, "day_offset": -10},
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    goal = create(
        tmp_db,
        primary_chart={
            "type": "chart",
            "kind": "line",
            "measure_id": actual.id,
            "expectation": {"op": "reference", "measure_id": derived.id, "day_offset": 0},
        },
    )
    monkeypatch.setattr(configuration, "MAX_DEMANDED_MEASURE_DATES", 5)
    with pytest.raises(PlannerError, match="5.*narrower"):
        views.readings_json(
            tmp_db,
            goal.id,
            datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin")),
            first_date="2026-10-26",
            last_date="2026-10-27",
        )


def test_goal_demand_budget_includes_legacy_future_values_and_counts_shared_pairs_once(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Future assembled values must not bypass the chart budget through the compatibility pass."""
    from planner.goals import configuration

    actual = manual_measure(tmp_db)
    derived = measure_actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="prior",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="Europe/Berlin",
            period=None,
            source_id=None,
            formula={"op": "reference", "measure_id": actual.id, "day_offset": -10},
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    goal = create(
        tmp_db,
        primary_chart={
            "type": "chart",
            "kind": "line",
            "measure_id": derived.id,
            "expectation": {"op": "constant", "value": 100},
        },
    )
    now = datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin"))
    for maximum in (1, 3):
        monkeypatch.setattr(configuration, "MAX_DEMANDED_MEASURE_DATES", maximum)
        with pytest.raises(PlannerError, match=rf"{maximum}.*narrower"):
            views.readings_json(
                tmp_db, goal.id, now, first_date="2026-10-28", last_date="2026-10-29"
            )
    monkeypatch.setattr(configuration, "MAX_DEMANDED_MEASURE_DATES", 4)
    bounded = views.readings_json(
        tmp_db, goal.id, now, first_date="2026-10-28", last_date="2026-10-29"
    )
    assert len(bounded["values"]) == 2

    document = page()
    document["blocks"].append(
        {
            "type": "chart",
            "id": "shared",
            "kind": "number",
            "measure_id": actual.id,
            "expectation": {"op": "reference", "measure_id": actual.id, "day_offset": -10},
        }
    )
    actions.update_goal(
        tmp_db,
        goal.id,
        {"expected_revision": 1, "page": document},
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    monkeypatch.setattr(configuration, "MAX_DEMANDED_MEASURE_DATES", 5)
    with pytest.raises(PlannerError, match="5.*narrower"):
        views.readings_json(tmp_db, goal.id, now, first_date="2026-10-28", last_date="2026-10-29")
    monkeypatch.setattr(configuration, "MAX_DEMANDED_MEASURE_DATES", 6)
    shared = views.readings_json(
        tmp_db, goal.id, now, first_date="2026-10-28", last_date="2026-10-29"
    )
    assert len(shared["values"]) == 4 and len(shared["charts"]) == 2


def test_future_actual_requires_retained_data_and_expectation_removal_restores_target(
    tmp_db: Connection,
) -> None:
    """An assembled future value is not an actual, but a real retained future value is."""
    identifier = source(tmp_db)
    actual = taken(tmp_db, identifier)
    run(tmp_db, identifier)
    collect(
        tmp_db,
        identifier,
        (
            CandidateObservation(actual.id, "2026-10-27", 12),
            CandidateObservation(actual.id, "2026-10-28", 15),
        ),
    )
    derived = measure_actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="dated phase",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="Europe/Berlin",
            period=None,
            source_id=None,
            formula={
                "op": "if_before",
                "date": "2026-10-28",
                "before": {"op": "constant", "value": 10},
                "otherwise": {"op": "constant", "value": 20},
            },
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    document = page()
    document["blocks"].append(
        {
            "type": "chart",
            "id": "derived",
            "kind": "number",
            "measure_id": derived.id,
            "expectation": {"op": "reference", "measure_id": derived.id, "day_offset": 0},
        }
    )
    goal = create(
        tmp_db,
        kind="quantitative",
        page=document,
        primary_chart={
            "type": "chart",
            "kind": "line",
            "measure_id": actual.id,
            "target": 1,
            "direction": "atmost",
            "expectation": {"op": "constant", "value": 12},
        },
    )
    assert goal.primary_chart is not None
    chart_id = goal.primary_chart["id"]
    now = datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin"))
    reading = views.readings_json(
        tmp_db, goal.id, now, first_date="2026-10-27", last_date="2026-10-28"
    )
    assert reading["charts"][chart_id]["points"][0]["meets_expectation"] is True
    assert reading["charts"][chart_id]["points"][1]["actual"]["value"] == 15
    assert reading["charts"][chart_id]["points"][1]["meets_expectation"] is False
    assert reading["charts"]["derived"]["points"][0]["actual"]["value"] == 10
    assert reading["charts"]["derived"]["points"][1]["actual"]["value"] is None
    assert reading["charts"]["derived"]["points"][1]["expected"]["value"] == 20
    assert reading["charts"]["derived"]["points"][1]["break_before"] is True
    primary = deepcopy(goal.primary_chart)
    del primary["expectation"]
    actions.update_goal(
        tmp_db,
        goal.id,
        {"expected_revision": 1, "primary_chart": primary},
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    legacy = views.readings_json(tmp_db, goal.id, now)
    assert chart_id not in legacy["charts"]
    assert legacy["status"] == "bad"


def test_goal_projection_preserves_other_charts_when_dependency_depth_exceeds_interpreter(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Demand discovery must return arithmetic_limit, with other charts still available."""
    measure = manual_measure(tmp_db)
    deep = measure_actions.create_measure(
        tmp_db,
        CreateMeasureBody(
            project_id="project_vylo",
            name="deep",
            unit="items",
            origin=MeasureOrigin.assembled,
            kind=MeasureKind.reading,
            timezone="Europe/Berlin",
            period=None,
            source_id=None,
            formula={"op": "constant", "value": 1},
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    document = page()
    document["blocks"].append(
        {
            "id": "unaffected",
            "type": "chart",
            "kind": "number",
            "measure_id": measure.id,
            "expectation": {"op": "constant", "value": 7},
        }
    )
    goal = create(
        tmp_db,
        page=document,
        primary_chart={
            "type": "chart",
            "kind": "number",
            "measure_id": measure.id,
            "expectation": {"op": "reference", "measure_id": deep.id, "day_offset": 0},
        },
    )
    assert goal.primary_chart is not None
    # Model a long current definition graph without hundreds of SQLite writes.
    definitions = {measure.id: measure}
    for index in range(300):
        identifier = deep.id if index == 0 else f"deep-{index}"
        formula = (
            {"op": "reference", "measure_id": f"deep-{index + 1}", "day_offset": 0}
            if index < 299
            else {"op": "constant", "value": 1}
        )
        definitions[identifier] = replace(deep, id=identifier, formula=cast(Any, formula))
    monkeypatch.setattr(views, "read_definitions", lambda *args, **kwargs: definitions)
    previous_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(200)
        reading = views.readings_json(
            tmp_db,
            goal.id,
            datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Berlin")),
            first_date="2026-10-27",
            last_date="2026-10-27",
        )
    finally:
        sys.setrecursionlimit(previous_limit)
    assert (
        reading["charts"][goal.primary_chart["id"]]["points"][0]["expected"]["status"]
        == "arithmetic_limit"
    )
    assert reading["charts"]["unaffected"]["points"][0]["expected"]["value"] == 7


def test_projection_explicit_minimum_date_does_not_underflow_default_window() -> None:
    """Valid explicit bounds near year one must not evaluate an unused rolling window."""
    from planner.goals.logic.projection import projection_bounds

    assert projection_bounds("0001-01-01", None, None, "0001-01-01", "0001-01-02") == (
        "0001-01-01",
        "0001-01-02",
        2,
    )


def test_period_coverage_survives_legacy_targets_references_and_formula_expectations(
    tmp_db: Connection,
) -> None:
    """Coverage facts survive numeric target, reference, and expectation comparisons."""
    from planner.measure_sources.records import encode_outputs
    from planner.measures.data import prepare_outputs

    identifier = source(tmp_db)
    actual = taken(tmp_db, identifier, kind=MeasureKind.period_count, period=CountPeriod.day)
    actual = measure_actions.update_measure(
        tmp_db,
        actual.id,
        {"expected_revision": actual.definition_revision, "timezone": "UTC"},
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    run(tmp_db, identifier)
    tmp_db.execute(
        "UPDATE measure_source_runs SET required_outputs=? WHERE id='run'",
        (encode_outputs(prepare_outputs(tmp_db, identifier, frozen_at=NOW)),),
    )
    tmp_db.commit()
    zone = ZoneInfo("UTC")
    partial_times = {
        "2026-09-29": (6, 11, 26),
        "2026-10-29": (21, 4, 10),
    }
    candidates = [CandidateObservation(actual.id, "2026-10-27", 0)]
    for day, clock in partial_times.items():
        start = datetime.fromisoformat(day).replace(tzinfo=zone)
        end = start.replace(hour=clock[0], minute=clock[1], second=clock[2])
        if day == "2026-09-29":
            start, end = end, start + timedelta(days=1)
        candidates.append(
            CandidateObservation(
                actual.id, day, 0, query_start_at=start.timestamp(), query_end_at=end.timestamp()
            )
        )
    full_start = datetime(2026, 10, 28, tzinfo=zone).timestamp()
    full_end = datetime(2026, 10, 29, tzinfo=zone).timestamp()
    candidates.append(
        CandidateObservation(
            actual.id, "2026-10-28", 0, query_start_at=full_start, query_end_at=full_end
        )
    )
    collect(tmp_db, identifier, tuple(candidates))
    document = page()
    document["blocks"].extend(
        {
            "type": "chart",
            "id": direction,
            "kind": "line",
            "measure_id": actual.id,
            "direction": direction,
            "expectation": {"op": "constant", "value": 5},
        }
        for direction in ("atmost", "atleast")
    )
    document["blocks"].extend(
        {
            "type": "chart",
            "id": f"{coverage}-comparison",
            "kind": "number",
            "measure_id": actual.id,
            "direction": "atmost",
            "expectation": {"op": "reference_at", "measure_id": actual.id, "date": comparison_day},
        }
        for coverage, comparison_day in (("partial", "2026-09-29"), ("unknown", "2026-10-27"))
    )
    goal = create(
        tmp_db,
        kind="quantitative",
        page=document,
        primary_chart={
            "type": "chart",
            "kind": "line",
            "measure_id": actual.id,
            "direction": "atmost",
            "reference": [
                {"date": "2026-09-29", "value": 5},
                {"date": "2026-10-29", "value": 5},
            ],
            "target": 5,
        },
    )
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    for day, coverage in (
        ("2026-09-29", "partial"),
        ("2026-10-27", "unknown"),
        ("2026-10-28", "complete"),
        ("2026-10-29", "partial"),
    ):
        reading = views.readings_json(tmp_db, goal.id, now, first_date=day, last_date=day)
        observed = reading["values"][0]
        assert observed["value"] == 0
        assert observed["coverage_status"] == coverage
        assert reading["status"] == "good"
        if day in partial_times:
            boundary = "query_start_at" if day == "2026-09-29" else "query_end_at"
            assert datetime.fromtimestamp(observed[boundary], zone).time().isoformat() == (
                ":".join(f"{part:02d}" for part in partial_times[day])
            )
            assert observed["coverage_reasons"]
        for direction in ("atmost", "atleast"):
            point = reading["charts"][direction]["points"][0]
            assert point["difference"] == -5
            assert point["meets_expectation"] == (direction == "atmost")
        for comparison_coverage in ("partial", "unknown"):
            comparison = reading["charts"][f"{comparison_coverage}-comparison"]["points"][0]
            assert comparison["expected"]["coverage_status"] == comparison_coverage
            assert comparison["difference"] == 0
            assert comparison["meets_expectation"] is True


def test_legacy_sum_keeps_coverage_and_missing_contributors() -> None:
    """Available subtotals compare while missing contributors retain their existing arithmetic."""
    from planner.goals.logic.status import chart_status, chart_summary

    chart: ChartBlock = {
        "type": "chart",
        "id": "sum",
        "kind": "bar",
        "placement": "primary",
        "measure_id": "count",
        "aggregate": "sum",
        "direction": "atmost",
        "target": 5,
    }
    complete = MeasureValue(
        "count",
        "2026-10-29",
        1,
        MeasureReadStatus.value,
        0,
        (),
        (),
        coverage_status=ScalarCoverageStatus.complete,
    )
    assert chart_status(chart, [complete]) == "good"
    for earlier in (
        replace(complete, date="2026-10-28", coverage_status=ScalarCoverageStatus.partial),
        replace(complete, date="2026-10-28", coverage_status=ScalarCoverageStatus.unknown),
        replace(complete, date="2026-10-28", value=None, status=MeasureReadStatus.missing_input),
    ):
        assert chart_summary(chart, [earlier, complete]) == (0, 10)
        assert chart_status(chart, [earlier, complete]) == "good"
        latest_chart = chart.copy()
        del latest_chart["aggregate"]
        assert chart_status(latest_chart, [earlier, complete]) == "good"
    assert chart_summary(chart, [earlier]) == (None, 5)
    assert chart_status(chart, [earlier]) == "neutral"
