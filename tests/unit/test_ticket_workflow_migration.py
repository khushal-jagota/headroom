"""The release baseline includes staging's saved workflows and background tasks."""

import json
from contextlib import closing
from pathlib import Path

from planner.core import db


def test_fresh_baseline_keeps_workflow_and_background_task_schema(tmp_path: Path) -> None:
    with closing(db.connect(str(tmp_path / "fresh.db"))) as conn:
        db.create_schema(conn)
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(tickets)")}
        assert columns["worker_type"][3] == 0
        assert columns["workflow_json"][3] == 1
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='conversation_background_tasks'"
        ).fetchone()
        definitions = [
            json.loads(row[0]) for row in conn.execute("SELECT definition_json FROM worker_types")
        ]
        assert definitions
        assert all("guidance" in definition for definition in definitions)
        assert all(
            "instructions" in stage for definition in definitions for stage in definition["stages"]
        )
