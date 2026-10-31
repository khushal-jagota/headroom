"""Run custody and assignment commit atomically before native dispatch."""

from __future__ import annotations

from sqlite3 import Connection

import pytest

from planner.execution_identity import ChildReference


def test_assignment_rolls_back_if_run_cannot_be_saved(tmp_db: Connection) -> None:
    """Run custody and assignment must commit together before transport."""
    from planner.executor.store import ExecutorStore

    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    child = ChildReference("22222222-2222-2222-2222-222222222222", "child", "c")
    tmp_db.execute(
        "CREATE TRIGGER refuse_run BEFORE INSERT ON mac_runs "
        "BEGIN SELECT RAISE(ABORT,'run write failed'); END"
    )
    tmp_db.commit()
    with pytest.raises(Exception, match="run write failed"):
        ExecutorStore(path).register_child(child, "chief", "chief", 1)
    assert tmp_db.execute("SELECT COUNT(*) FROM executor_child_assignments").fetchone()[0] == 0
    assert tmp_db.execute("SELECT COUNT(*) FROM mac_runs").fetchone()[0] == 0
