"""New boundary risks: document races, independent state, and live measure projections."""

from copy import deepcopy
from datetime import datetime
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
from planner.goals.contracts import ChartBlock, ChecklistBlock, Goal, GoalsResponse
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
    CreateMeasureBody,
    MeasureKind,
    MeasureOrigin,
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
    assert accepted.active and accepted.completed
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
    assert inactive.completed and not data.list_goals(tmp_db, active=True)
    reactivated = actions.update_goal(
        tmp_db, goal.id, {"expected_revision": 3, "active": True}, principal=agent, now=NOW + 4
    )
    assert reactivated.active and reactivated.completed
    reversed_goal = actions.update_goal(
        tmp_db, goal.id, {"expected_revision": 4, "completed": False}, principal=agent, now=NOW + 5
    )
    assert reversed_goal.active and not reversed_goal.completed
    other = create(tmp_db, title="Another goal")
    with pytest.raises(PlannerError):
        actions.update_goal(
            tmp_db,
            other.id,
            {"expected_revision": 1, "active": False},
            principal=agent,
            now=NOW + 6,
        )


def test_projection_keeps_signed_gaps_and_refresh_does_not_change_document(
    tmp_db: Connection,
) -> None:
    source_id = source(tmp_db)
    measure = taken(tmp_db, source_id)
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
    goal = create(tmp_db, page=document)
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
