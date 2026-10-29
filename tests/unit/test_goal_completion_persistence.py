"""Writer transitions preserve one binary input and project selected checklist completion."""

import asyncio
import json
import shutil
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from tests.unit.test_db import _migration_tree_through
from tests.unit.test_goal_completion import page
from tests.unit.test_measure_persistence import NOW

from planner.core import db
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL, PlannerError
from planner.goals import actions, api, data, views
from planner.goals.contracts import ChecklistBlock, Goal
from planner.goals.logic.completion import effective_completion, selected_checklist


def create(conn: Connection, **extra: Any) -> Goal:
    checklist: ChecklistBlock = {
        "type": "checklist",
        "id": "delivery",
        "items": [{"id": "draft", "label": "Draft", "state": "done"}],
    }
    return actions.create_goal(
        conn,
        {
            "project_id": "project_vylo",
            "title": "Ship the guide",
            "page": page(checklist),
            "primary_checklist_block_id": checklist["id"],
            **extra,
        },
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )


def edit(conn: Connection, goal: Goal, **changes: Any) -> Goal:
    return actions.update_goal(
        conn,
        goal.id,
        {"expected_revision": goal.revision, **changes},
        principal=OWNER_PRINCIPAL,
        now=NOW + goal.revision,
    )


def test_saved_items_drive_completion_without_synchronizing_binary_input(
    tmp_db: Connection,
) -> None:
    goal = create(tmp_db)
    goal = edit(tmp_db, goal, completed=True)
    document = deepcopy(goal.page)
    checklist = selected_checklist(document, "delivery")
    assert checklist is not None
    checklist["items"][0]["state"] = "open"
    goal = edit(tmp_db, goal, page=document, completed=False)
    assert not views.goal_json(tmp_db, goal, datetime.fromtimestamp(NOW, UTC))["completed"]
    checklist["items"][0]["state"] = "done"
    checklist["items"].append({"id": "publish", "label": "Publish", "state": "blocked"})
    goal = edit(tmp_db, goal, page=document)
    assert not effective_completion(goal.binary_checked, goal.page, "delivery")
    checklist["items"].pop()
    goal = edit(tmp_db, goal, page=document, completed=True)
    assert effective_completion(goal.binary_checked, goal.page, "delivery")
    checklist["items"].clear()
    goal = edit(tmp_db, goal, page=document)
    assert not effective_completion(goal.binary_checked, goal.page, "delivery")
    assert not goal.binary_checked
    stored = tmp_db.execute("SELECT completed,page FROM goals WHERE id=?", (goal.id,)).fetchone()
    assert stored["completed"] == 0
    assert json.loads(stored["page"]) == document


def test_reselection_uses_resulting_page_and_ordinary_checklists_stay_independent(
    tmp_db: Connection,
) -> None:
    goal = create(tmp_db, completed=True)
    document = deepcopy(goal.page)
    ordinary: ChecklistBlock = {
        "type": "checklist",
        "id": "review",
        "items": [{"id": "read", "label": "Read", "state": "open"}],
    }
    document["blocks"].append(ordinary)
    goal = edit(tmp_db, goal, page=document)
    assert effective_completion(goal.binary_checked, goal.page, "delivery")
    goal = edit(tmp_db, goal, primary_checklist_block_id="review", completed=False)
    assert not effective_completion(goal.binary_checked, goal.page, "review")
    assert not goal.binary_checked  # A matching completed value never writes the binary input.
    ordinary["items"][0]["state"] = "done"
    ordinary["title"] = "Renamed"
    document["blocks"].reverse()
    goal = edit(tmp_db, goal, page=document, completed=True)
    assert goal.primary_checklist_block_id == "review"
    assert goal.page == document


@pytest.mark.parametrize("transition", ["clear", "remove", "quantitative", "explicit_binary"])
def test_selection_end_snapshots_previous_effective_completion(
    tmp_db: Connection,
    transition: str,
) -> None:
    goal = create(tmp_db)
    document = deepcopy(goal.page)
    checklist = selected_checklist(document, "delivery")
    assert checklist is not None
    checklist["items"][0]["state"] = "open"
    changes: dict[str, Any] = {"page": document}
    if transition == "remove":
        document["blocks"] = []
    elif transition == "quantitative":
        changes["kind"] = "quantitative"
    else:
        changes["primary_checklist_block_id"] = None
        if transition == "explicit_binary":
            changes["completed"] = False
    goal = edit(tmp_db, goal, **changes)
    assert goal.primary_checklist_block_id is None
    assert goal.binary_checked is (transition != "explicit_binary")
    assert goal.page == document


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"primary_checklist_block_id": "missing"}, "saved checklist"),
        ({"primary_checklist_block_id": "section"}, "saved checklist"),
        ({"primary_checklist_block_id": 4}, "text or null"),
        ({"kind": "quantitative", "primary_checklist_block_id": "delivery"}, "binary goals"),
        ({"completed": False}, "completion follows"),
        ({"completed": None}, "booleans"),
    ],
)
def test_invalid_selected_completion_edits_are_atomic(
    tmp_db: Connection,
    changes: dict[str, Any],
    message: str,
) -> None:
    goal = create(tmp_db)
    with pytest.raises(PlannerError, match=message):
        edit(tmp_db, goal, title="Rejected title", **changes)
    assert data.read_goal(tmp_db, goal.id) == goal


def test_stale_page_cannot_change_selected_completion(tmp_db: Connection) -> None:
    initial = create(tmp_db)
    document = deepcopy(initial.page)
    checklist = selected_checklist(document, "delivery")
    assert checklist is not None
    checklist["items"][0]["state"] = "open"
    accepted = edit(tmp_db, initial, page=document)
    with pytest.raises(PlannerError, match="goal changed"):
        edit(tmp_db, initial, page=initial.page, completed=True)
    assert data.read_goal(tmp_db, initial.id) == accepted


def test_write_detail_list_and_readings_project_the_same_completion(tmp_db: Connection) -> None:
    goal = create(tmp_db, deadline="2026-10-27")
    clock = FakeClock(datetime(2026, 10, 28, 12, tzinfo=UTC))
    context = RequestContext(OWNER_PRINCIPAL)

    async def responses(completed: bool) -> None:
        saved = data.read_goal(tmp_db, goal.id)
        write = await api.update_goal(
            goal.id,
            {"expected_revision": saved.revision, "completed": completed},
            tmp_db,
            context,
            clock,
        )
        detail = await api.read_goal(goal.id, tmp_db, clock)
        listing = await api.list_goals(tmp_db, clock)
        readings = await api.readings(goal.id, tmp_db, clock)
        assert write == detail == listing["goals"][0]
        assert detail["completed"] is completed
        assert "binary_checked" not in detail
        assert readings["status"] == detail["status"] == ("good" if completed else "bad")
        assert readings["charts"] == {} and readings["values"] == []

    asyncio.run(responses(True))
    saved = data.read_goal(tmp_db, goal.id)
    document = deepcopy(saved.page)
    checklist = selected_checklist(document, "delivery")
    assert checklist is not None
    checklist["items"][0]["state"] = "open"
    edit(tmp_db, saved, page=document)
    asyncio.run(responses(False))


def test_selector_migration_preserves_binary_inputs_and_saved_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "manual_measures")
    document = page({"type": "checklist", "id": "delivery", "items": []})
    with closing(db.connect(str(tmp_path / "before-selector.db"))) as conn:
        db.create_schema(conn)
        for checked in (0, 1):
            conn.execute(
                "INSERT INTO goals(id,project_id,title,why,kind,active,completed,page,revision,"
                "created_at,updated_at,employee_backend,employee_launch_model) "
                "VALUES(?,'project_vylo','Retained','','binary',1,?,?,1,1,1,'codex','gpt-6.1-sol')",
                (f"retained-{checked}", checked, json.dumps(document)),
            )
        before = [dict(row) for row in conn.execute("SELECT * FROM goals ORDER BY id")]
        conn.commit()
        shutil.copy(
            shipped / "versions/primary_goal_checklist.py",
            tree / "versions/primary_goal_checklist.py",
        )
        db.create_schema(conn)
        after = [dict(row) for row in conn.execute("SELECT * FROM goals ORDER BY id")]
        assert after == [{**row, "primary_checklist_block_id": None} for row in before]
        for checked in (False, True):
            goal = data.read_goal(conn, f"retained-{int(checked)}")
            assert goal.binary_checked is checked and goal.page == document
            assert (
                views.goal_json(conn, goal, datetime.fromtimestamp(NOW, UTC))["completed"]
                is checked
            )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
