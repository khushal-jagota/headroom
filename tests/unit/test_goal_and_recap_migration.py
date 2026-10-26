"""The real upgrade must carry every orchestrator's existing text into its goal.

A database built from nothing has no orchestrators while the chain runs, so no existing
suite watches an occupied `sprint_items` travel through it. That is the one thing this
migration has to get right, and it is also where the chain reaches into current code:
`transactional_navigation` rebuilds the navigation projection mid-flight, before the
rename, and it does so at the prepared shape once `priority` is gone.
"""

import json
from contextlib import closing
from pathlib import Path

import pytest
from tests.support.navigation_current import current_navigation_database
from tests.unit.test_db import _migration_tree_through

from planner.core import db

_ITEMS = (
    ("si_kept", "Carried orchestrator", "  Work out what calendar context buys us.\n\tKeep it."),
    ("si_unicode", "Unicode orchestrator", '雪 · "quoted" \\ 😀 trailing spaces   '),
    ("si_blank", "Blank orchestrator", ""),
)


def test_occupied_orchestrators_keep_their_text_as_goal_with_an_empty_recap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "occupied-orchestrators.db"
    with closing(db.connect(str(database))) as conn:
        # `remove_priority` is the shape a deployed database sits at before the whole
        # navigation branch and this rename, so it is the one that proves the journey.
        with monkeypatch.context() as historical:
            _migration_tree_through(tmp_path, historical, "remove_priority")
            db.create_schema(conn)
        for item_id, title, body in _ITEMS:
            conn.execute(
                "INSERT INTO sprint_items(id,title,body,project_id,supervisor_backend,"
                "supervisor_model,supervisor_reasoning_effort,created_at,updated_at) "
                "VALUES (?,?,?,'project_other','codex','sol','medium',1,1)",
                (item_id, title, body),
            )
        conn.commit()

        db.create_schema(conn)

        stored = dict(conn.execute("SELECT id, goal FROM managers").fetchall())
        assert stored == {item_id: body for item_id, _, body in _ITEMS}
        assert {row[0] for row in conn.execute("SELECT DISTINCT recap FROM managers")} == {""}
        columns = [row[1] for row in conn.execute("PRAGMA table_info(managers)")]
        assert "body" not in columns

    # The navigation projection is written earlier in this chain, from the only column
    # there was then, so every orchestrator record holds a goal under the name recap
    # until installation rebuilds it. Installation is what serves a reader, so that is
    # where an empty recap has to be true.
    with current_navigation_database(database) as installed:
        projected = {
            str(row[0]): json.loads(str(row[1]))
            for row in installed.execute(
                "SELECT id, value FROM navigation_records WHERE kind = 'manager'"
            )
        }
    assert set(projected) == {item_id for item_id, _, _ in _ITEMS}
    assert {record["recap"] for record in projected.values()} == {""}
    assert {record["recap_preview"] for record in projected.values()} == {""}
