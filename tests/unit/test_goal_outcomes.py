"""Completion dates and global outcomes protect facts absent from chart status."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Any, cast

import pytest
from tests.support.navigation_current import current_navigation_database
from tests.unit.test_goal_pages import create, numeric_rule
from tests.unit.test_measure_persistence import NOW
from tests.unit.test_navigation_goal_completion import navigation_goal

from planner.core.contracts import OWNER_PRINCIPAL, PlannerError
from planner.goals import actions, data, views
from planner.goals.contracts import ChecklistBlock, Goal
from planner.goals.logic.completion import goal_completed
from planner.goals.logic.outcomes import goal_outcome, outcome_summary


def edit(conn: Connection, goal: Goal, *, now: int = NOW, **changes: Any) -> Goal:
    return actions.update_goal(
        conn,
        goal.id,
        {"expected_revision": goal.revision, **changes},
        principal=OWNER_PRINCIPAL,
        now=now,
    )


def test_accepted_completion_dates_follow_transitions_and_keep_unknown_history(
    tmp_db: Connection,
) -> None:
    goal = create(tmp_db, completed=True)
    assert goal.completion_date == "2026-10-27"
    unchanged = edit(tmp_db, goal, now=NOW + 86400, active=False, title="Retained date")
    assert unchanged.completion_date == goal.completion_date
    reopened = edit(tmp_db, unchanged, now=NOW + 86400, completed=False)
    assert reopened.completion_date is None and not reopened.active
    completed = edit(tmp_db, reopened, now=NOW + 2 * 86400, completed=True)
    assert completed.completion_date == "2026-10-29" and not completed.active
    with pytest.raises(PlannerError, match="goal changed"):
        edit(tmp_db, reopened, completed=False)
    assert data.read_goal(tmp_db, completed.id) == completed

    # An already-complete legacy record keeps its unknown date on ordinary edits.
    tmp_db.execute("UPDATE goals SET completion_date=NULL WHERE id=?", (completed.id,))
    tmp_db.commit()
    legacy = data.read_goal(tmp_db, completed.id)
    assert edit(tmp_db, legacy, title="Legacy completion").completion_date is None

    checklist = create(
        tmp_db,
        primary_checklist_block_id="delivery",
        page={
            "kind": "headroom.page",
            "version": 1,
            "title": "Delivery",
            "summary": "",
            "blocks": [
                {
                    "type": "checklist",
                    "id": "delivery",
                    "items": [
                        {"id": "ship", "label": "Ship", "state": "done"},
                    ],
                }
            ],
        },
    )
    assert checklist.completion_date == "2026-10-27"
    document = deepcopy(checklist.page)
    cast(ChecklistBlock, document["blocks"][0])["items"].append(
        {"id": "review", "label": "Review", "state": "open"}
    )
    assert edit(tmp_db, checklist, page=document).completion_date is None


def test_current_deadline_results_and_global_rate_ignore_list_filters(tmp_db: Connection) -> None:
    hit = create(tmp_db, completed=True, active=False, deadline="2026-10-27")
    miss = create(tmp_db, deadline="2026-10-26", project_id="project_other")
    pending = create(tmp_db, deadline="2026-10-27")
    undated = create(tmp_db, completed=True)
    unknown = replace(hit, completion_date=None)
    assert goal_outcome(hit, "2026-10-27") == "hit"
    assert goal_outcome(replace(hit, deadline="2026-10-26"), "2026-10-27") == "miss"
    assert goal_outcome(pending, "2026-10-27") == "pending"
    assert goal_outcome(pending, "2026-10-28") == "miss"
    assert goal_outcome(undated, "2026-10-27") == "undated"
    assert goal_outcome(unknown, "2026-10-27") == "unknown"
    assert outcome_summary([pending, undated, unknown], "2026-10-27") == {
        "hits": 0,
        "misses": 0,
        "rate": None,
    }
    assert outcome_summary([hit, miss, pending, undated, unknown], "2026-10-27") == {
        "hits": 1,
        "misses": 1,
        "rate": 0.5,
    }
    filtered = views.list_json(
        tmp_db, datetime.fromtimestamp(NOW, UTC), active=True, project_id="project_vylo"
    )
    assert hit.id not in {goal["id"] for goal in filtered["goals"]}
    assert miss.id not in {goal["id"] for goal in filtered["goals"]}
    assert filtered["outcome_summary"] == {"hits": 1, "misses": 1, "rate": 0.5}
    detail = views.goal_json(tmp_db, hit, datetime.fromtimestamp(NOW, UTC))
    assert detail["completed"] and detail["completion_date"] == "2026-10-27"
    assert detail["outcome"] == "hit"


def test_numeric_rule_owns_completion_and_legacy_edits_keep_their_facts(tmp_db: Connection) -> None:
    body = {
        "project_id": "project_vylo",
        "title": "Revenue",
        "kind": "quantitative",
        "primary_chart": {"type": "chart", "kind": "number"},
    }
    with pytest.raises(PlannerError, match="completion_rule"):
        actions.create_goal(tmp_db, body, principal=OWNER_PRINCIPAL, now=NOW)
    rule = numeric_rule(tmp_db)
    goal = actions.create_goal(
        tmp_db, {**body, "completion_rule": rule}, principal=OWNER_PRINCIPAL, now=NOW
    )
    assert not goal_completed(goal) and goal.completion_date is None
    assert edit(tmp_db, goal, completed=False).completion_date is None
    goal = data.read_goal(tmp_db, goal.id)
    with pytest.raises(PlannerError, match="completion follows"):
        edit(tmp_db, goal, completed=True)
    with pytest.raises(PlannerError, match="completion_rule"):
        edit(tmp_db, goal, completion_rule=None)
    binary = create(tmp_db)
    with pytest.raises(PlannerError, match="only to quantitative"):
        edit(tmp_db, binary, completion_rule=rule)
    with pytest.raises(PlannerError, match="completion_rule"):
        edit(tmp_db, binary, kind="quantitative", primary_chart=body["primary_chart"])

    # Simulate a pre-feature numeric record without inventing its completion date.
    tmp_db.execute("UPDATE goals SET completion_rule=NULL,completed=1 WHERE id=?", (goal.id,))
    tmp_db.commit()
    legacy = data.read_goal(tmp_db, goal.id)
    retained = edit(tmp_db, legacy, title="Legacy revenue", deadline="2026-10-27")
    assert goal_completed(retained) and retained.completion_date is None
    assert goal_outcome(retained, "2026-10-27") == "unknown"


def test_navigation_projects_automatic_completion_date_without_binary_flag(
    tmp_db: Connection,
) -> None:
    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    with current_navigation_database(database) as conn:
        goal = create(conn, kind="quantitative", primary_chart={"type": "chart", "kind": "number"})
        assert not navigation_goal(conn, goal.id)["completed"]
        conn.execute(
            "UPDATE goals SET completion_date='2026-10-27',revision=revision+1 WHERE id=?",
            (goal.id,),
        )
        conn.commit()
        assert navigation_goal(conn, goal.id)["completed"]
        assert not data.read_goal(conn, goal.id).binary_checked
