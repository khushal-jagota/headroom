"""New boundary risks: document races, independent state, and live measure projections."""

from copy import deepcopy
from datetime import datetime
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from starlette.requests import Request
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
from planner.measures.contracts import CandidateObservation


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
