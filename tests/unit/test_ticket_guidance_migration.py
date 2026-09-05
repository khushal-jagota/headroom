"""One cutover preserves every historical note and rejects corruption before writes."""

from __future__ import annotations

import json
from pathlib import Path
from sqlite3 import Connection

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import connect


def _parent(path: Path) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), "planning_day_direction")
    finally:
        engine.dispose()


def _insert(conn: Connection, ticket_id: str, fields: object) -> None:
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, stage, ceiling, "
        "fields, created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'codex', 'done', 'done', ?, 11, 12)",
        (ticket_id, ticket_id, json.dumps(fields)),
    )


def test_cutover_preserves_both_note_sources_exactly_and_all_other_columns(tmp_path: Path) -> None:
    path = tmp_path / "guidance.db"
    _parent(path)
    conn = connect(str(path))
    fields: dict[str, dict[str, object]] = {
        "plan": {
            "value": "approved",
            "proposal": {"body": "pending", "proposed_by": "agent", "created_at": 9},
            "user_note": "  same\n",
            "notes": "  same\n",
        },
        "unknown_old_stage": {
            "value": None,
            "proposal": None,
            "user_note": None,
            "notes": "\tmasked\n\n",
            "historical_metadata": "keep",
        },
        "implementation": {"value": "code", "proposal": None, "user_note": "   "},
        "empty": {"value": None, "proposal": None, "user_note": "", "notes": None},
    }
    _insert(conn, "t_old", fields)
    conn.execute(
        "INSERT INTO projects (id, name, summary, created_at, updated_at) "
        "VALUES ('p', 'P', '', 1, 1)"
    )
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, "
        "workspace_folder, access, created_at) "
        "VALUES ('conv', 'codex', 'model', '/work', 'full', 1)"
    )
    conn.execute("UPDATE tickets SET project_id = 'p', conversation_id = 'conv' WHERE id = 't_old'")
    conn.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp', 'Sprint', '2026-09-01', '2026-09-14', 1, 1)"
    )
    conn.execute(
        "INSERT INTO sprint_items (id, title, project_id, sprint_id, created_at, updated_at) "
        "VALUES ('si', 'Outcome', 'p', 'sp', 1, 1)"
    )
    conn.execute("UPDATE tickets SET sprint_item_id = 'si', sprint_id = 'sp' WHERE id = 't_old'")
    conn.execute("INSERT INTO days (id, created_at, updated_at) VALUES ('day_2026-09-05', 1, 1)")
    conn.execute(
        "INSERT INTO day_tickets (day_id, ticket_id, position) "
        "VALUES ('day_2026-09-05', 't_old', 3)"
    )
    before = dict(conn.execute("SELECT * FROM tickets WHERE id = 't_old'").fetchone())
    _insert(conn, "t_empty", {})
    _upgrade_legacy(conn)
    after = dict(conn.execute("SELECT * FROM tickets WHERE id = 't_old'").fetchone())
    assert after.pop("guidance") == (
        "## plan\n\n### user_note\n\n  same\n\n\n### notes\n\n  same\n"
        "\n\n## unknown_old_stage\n\n\tmasked\n\n"
        "\n\n## implementation\n\n   "
    )
    converted = json.loads(after.pop("fields"))
    before.pop("fields")
    assert after == before
    assert tuple(
        conn.execute("SELECT * FROM day_tickets WHERE ticket_id = 't_old'").fetchone()
    ) == ("day_2026-09-05", "t_old", 3)
    assert converted == {
        name: {key: value for key, value in slot.items() if key not in ("user_note", "notes")}
        for name, slot in fields.items()
    }
    assert conn.execute("SELECT guidance FROM tickets WHERE id = 't_empty'").fetchone()[0] == ""
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # An already upgraded database is unchanged on startup.
    _upgrade_legacy(conn)
    assert (
        json.loads(conn.execute("SELECT fields FROM tickets WHERE id = 't_old'").fetchone()[0])
        == converted
    )
    conn.close()


def test_corrupt_later_row_keeps_earlier_content_and_parent_schema(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    _parent(path)
    conn = connect(str(path))
    _insert(conn, "a_valid", {"plan": {"value": "keep", "user_note": "keep exactly"}})
    _insert(conn, "z_corrupt", {"plan": {"notes": 42}})
    before = [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")]
    with pytest.raises(ValueError, match="corrupt ticket guidance"):
        _upgrade_legacy(conn)
    assert [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")] == before
    assert "guidance" not in {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
    assert (
        conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == "planning_day_direction"
    )
    conn.close()


def _upgrade_legacy(conn: Connection) -> None:
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), "ticket_guidance")
    finally:
        engine.dispose()
