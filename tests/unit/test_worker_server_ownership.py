"""Preserve the exact server rule across fresh seeds and existing owner skill rows."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import op

from planner.core.migrations.server_ownership_correction_data import SERVER_OWNERSHIP_SECTION
from planner.core.migrations.versions import worker_server_ownership as migration


def test_worker_server_rule_seed_and_existing_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_section = """### The server is not yours

The Panels server that runs you is operator-owned. Khushal starts it, stops it and restarts it.

Do not signal it and do not replace it. Never run `kill`, `pkill`, `killall`, an `lsof`-driven
kill, or `systemctl` against it. Never run `panels serve` on its port.

A pattern cannot tell the live server from your own. On this machine the live deployment and a
scratch instance run the same command line, so `pkill -f "planner serve"` stops both. That took
the live server down on 15 August 2026 and again on 20 October 2026.

If you start your own server, record its PID at launch and stop that exact PID. If a port is
busy, pick another port. Never free a port.

If the live server must restart, say so and stop.
"""
    seed = (Path(__file__).parents[2] / "src/planner/skills/panels-worker/SKILL.md").read_text()
    assert expected_section in seed
    assert SERVER_OWNERSHIP_SECTION == expected_section

    engine = sa.create_engine("sqlite://")
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE managed_skills (skill_name TEXT PRIMARY KEY, "
            "source_text TEXT NOT NULL, updated_at INTEGER NOT NULL)"
        )
        # A heading alone must not suppress restoration of the complete rule.
        original = "Owner prose.\n\n### The server is not yours\n\nOwner partial guidance.\n"
        conn.exec_driver_sql(
            "INSERT INTO managed_skills VALUES (?, ?, ?)", ("panels-worker", original, 123)
        )
        other = ("panels", "Other owner prose.\n", 456)
        conn.exec_driver_sql("INSERT INTO managed_skills VALUES (?, ?, ?)", other)
        monkeypatch.setattr(op, "get_bind", lambda: conn)
        migration.upgrade()
        restored = ("panels-worker", original + "\n\n" + expected_section, 123)
        assert [
            tuple(row)
            for row in conn.exec_driver_sql("SELECT * FROM managed_skills ORDER BY skill_name")
        ] == [
            other,
            restored,
        ]

        # Reject even byte-identical updates when the complete section is present.
        conn.exec_driver_sql(
            "CREATE TRIGGER no_skill_updates BEFORE UPDATE ON managed_skills "
            "BEGIN SELECT RAISE(ABORT, 'unexpected skill update'); END"
        )
        migration.upgrade()
        assert [
            tuple(row)
            for row in conn.exec_driver_sql("SELECT * FROM managed_skills ORDER BY skill_name")
        ] == [
            other,
            restored,
        ]

        # Existing owner prose after a compliant section also remains untouched.
        conn.exec_driver_sql("DELETE FROM managed_skills WHERE skill_name = 'panels-worker'")
        compliant = (
            "panels-worker",
            "Owner opening prose.\n\n" + expected_section + "\nOwner closing prose.\n",
            789,
        )
        conn.exec_driver_sql("INSERT INTO managed_skills VALUES (?, ?, ?)", compliant)
        migration.upgrade()
        assert [
            tuple(row)
            for row in conn.exec_driver_sql("SELECT * FROM managed_skills ORDER BY skill_name")
        ] == [
            other,
            compliant,
        ]

        conn.exec_driver_sql("DELETE FROM managed_skills")
        migration.upgrade()
        assert conn.exec_driver_sql("SELECT * FROM managed_skills").all() == []
    engine.dispose()
