"""Navigation must derive saved checklist completion without expanding its payload."""

from copy import deepcopy
from pathlib import Path
from sqlite3 import Connection
from typing import cast

from tests.support.navigation_current import current_navigation_database

from planner.core.contracts import OWNER_PRINCIPAL
from planner.goals import actions
from planner.goals.contracts import ChecklistBlock, PageDocument, SectionBlock
from planner.navigation import data
from planner.navigation.contracts import GoalValue


def navigation_goal(conn: Connection, goal_id: str) -> GoalValue:
    return next(
        record["value"]
        for record in data.snapshot(conn)["records"]
        if record["kind"] == "goal" and record["id"] == goal_id
    )


def test_navigation_follows_selected_saved_checklist_on_commits(tmp_db: Connection) -> None:
    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    checklist: ChecklistBlock = {
        "type": "checklist",
        "id": "selected",
        "items": [{"id": "deliver", "label": "Deliver", "state": "done"}],
    }
    document: PageDocument = {
        "kind": "headroom.page",
        "version": 1,
        "title": "Delivery",
        "summary": "",
        "blocks": [
            {"type": "checklist", "id": "ordinary", "items": []},
            {
                "type": "section",
                "id": "section",
                "title": "Required",
                "blocks": [checklist],
            },
        ],
    }
    with current_navigation_database(database) as conn:
        goal = actions.create_goal(
            conn,
            {
                "project_id": "project_other",
                "title": "Delivery",
                "kind": "binary",
                "page": document,
                "primary_checklist_block_id": "selected",
            },
            principal=OWNER_PRINCIPAL,
            now=1,
        )
        assert not goal.binary_checked
        assert navigation_goal(conn, goal.id) == {
            "title": "Delivery",
            "active": True,
            "completed": True,
            "created_at": 1,
            "updated_at": 1,
            "conversation_id": None,
        }

        reopened = deepcopy(goal.page)
        reopened["blocks"].reverse()
        section = cast(SectionBlock, reopened["blocks"][0])
        selected = cast(ChecklistBlock, section["blocks"][0])
        selected["items"][0]["state"] = "open"
        # Return to binary input first, so the next checklist read must override True.
        binary = actions.update_goal(
            conn,
            goal.id,
            {"expected_revision": goal.revision, "primary_checklist_block_id": None},
            principal=OWNER_PRINCIPAL,
            now=2,
        )
        assert binary.binary_checked
        actions.update_goal(
            conn,
            goal.id,
            {
                "expected_revision": binary.revision,
                "primary_checklist_block_id": "selected",
                "page": reopened,
            },
            principal=OWNER_PRINCIPAL,
            now=3,
        )
        assert navigation_goal(conn, goal.id)["completed"] is False


def test_navigation_rebuild_before_selector_column_keeps_binary_input(tmp_db: Connection) -> None:
    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    with current_navigation_database(database) as conn:
        goal = actions.create_goal(
            conn,
            {"project_id": "project_other", "title": "Legacy binary", "kind": "binary",
             "primary_checklist_block_id": None, "completed": True},
            principal=OWNER_PRINCIPAL,
            now=1,
        )
        # The occupied upgrade journey lives in goal persistence coverage. This case
        # isolates navigation's rebuild against the pre-selector physical shape.
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE goals DROP COLUMN primary_checklist_block_id")
        data.rebuild(conn)
        conn.commit()
        assert navigation_goal(conn, goal.id)["completed"] is True
        conn.execute("UPDATE goals SET completed=0 WHERE id=?", (goal.id,))
        conn.commit()
        assert navigation_goal(conn, goal.id)["completed"] is False
