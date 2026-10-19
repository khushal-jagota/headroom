"""The real upgrade must preserve existing Chief settings and skill content."""

import shutil
from contextlib import closing
from pathlib import Path

import pytest
from tests.unit.test_db import _migration_tree_through

from planner.core import db


def test_chief_recap_upgrade_preserves_existing_settings_and_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "remove_ticket_retirement")
    with closing(db.connect(str(tmp_path / "before-recap.db"))) as conn:
        db.create_schema(conn)
        conn.execute(
            "UPDATE chief_settings SET employee_launch_model = ?, "
            "employee_launch_reasoning_effort = ?",
            ("custom-model", "high"),
        )
        conn.execute(
            "UPDATE managed_skills SET source_text = source_text || ? WHERE skill_name = ?",
            ("\n# An owner-edited Chief skill", "panels-chief-of-staff"),
        )
        before = tuple(conn.execute("SELECT * FROM chief_settings").fetchone())
        skill = tuple(
            conn.execute(
                "SELECT * FROM managed_skills WHERE skill_name = ?", ("panels-chief-of-staff",)
            ).fetchone()
        )
        conn.commit()
        shutil.copy(shipped / "versions/chief_recap.py", tree / "versions/chief_recap.py")
        db.create_schema(conn)
        assert tuple(conn.execute("SELECT * FROM chief_settings").fetchone()) == (*before, "")
        assert tuple(
            conn.execute(
                "SELECT * FROM managed_skills WHERE skill_name = ?", ("panels-chief-of-staff",)
            ).fetchone()
        ) == skill
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "chief_recap"
        )
