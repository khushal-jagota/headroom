"""The raw-field cutover preserves drafts and rolls back with its schema."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import event

from planner.core import db
from planner.core.db import connect, create_schema


def _parent(path: Path) -> None:
    engine = db._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db._alembic_config(connection), "ticket_guidance")
    finally:
        engine.dispose()


def _insert(
    path: Path,
    ticket_id: str,
    fields: dict[str, Any],
    *,
    stage: str = "needs_plan",
    status: str = "empty",
    worker_type: str = "coding",
) -> None:
    with connect(str(path)) as conn:
        conn.execute(
            "INSERT INTO tickets (id,title,worker_type,employee_backend,stage,ceiling,"
            "ticket_status,fields,created_at,updated_at,ticket_status_revision) "
            "VALUES (?,? ,?,'codex',?,'done',?,?,11,12,7)",
            (ticket_id, ticket_id, worker_type, stage, status, json.dumps(fields)),
        )


def _proposal(body: str = "draft") -> dict[str, Any]:
    return {"body": body, "proposed_by": "worker", "created_at": 17}


def _archive_sections(body: str) -> list[dict[str, Any]]:
    return [json.loads(section) for section in re.findall(r"`{3,}json\n(.*?)\n`{3,}", body, re.S)]


def test_cutover_keeps_current_saved_value_and_draft_and_archives_raw_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.db"
    _parent(path)
    current = {**_proposal("  π\n```\nkeep\t"), "unknown": {"nested": [None, True, " x "]}}
    fields: dict[str, Any] = {
        "plan": {"value": "previous settled value", "proposal": current, "extra": "kept"},
        "success": {
            "value": "",
            "proposal": _proposal("Old unapproved\n\n```python\nprint('π')\n```\n\n  tail\t"),
        },
        "retired": {
            "value": " historical\n",
            "proposal": _proposal("retired unapproved"),
            "other": {"nested": 9},
        },
        "retired_empty": {"value": None, "proposal": None},
    }
    _insert(path, "a", fields, status="awaiting_approval")
    _insert(
        path,
        "b",
        {"kickoff": {"value": None, "proposal": _proposal("")}},
        stage="needs_kickoff",
        status="awaiting_approval",
    )
    _insert(path, "c", {"kickoff": {"proposal": _proposal("terminal draft")}}, stage="dropped")
    with connect(str(path)) as conn:
        before = dict(conn.execute("SELECT * FROM tickets WHERE id='a'").fetchone())
        create_schema(conn)
        after = dict(conn.execute("SELECT * FROM tickets WHERE id='a'").fetchone())
        assert json.loads(after.pop("field_values")) == {
            "plan": "previous settled value",
            "success": "",
        }
        assert json.loads(after.pop("pending_proposal")) == {
            "field": "plan",
            **_proposal(current["body"]),
        }
        archive = after.pop("archived_field_content")
        sections = _archive_sections(archive)
        assert {"field": "plan", "content": {"unknown": current["unknown"]}} in sections
        assert {"field": "plan", "content": {"extra": "kept"}} in sections
        assert fields["success"]["proposal"]["body"] in archive
        assert fields["retired"]["value"] in archive
        assert fields["retired"]["proposal"]["body"] in archive
        assert {"field": "retired", "content": {"other": {"nested": 9}}} in sections
        assert "retired_empty" not in archive
        assert "Unapproved proposal" in archive
        assert after.pop("ceiling_holder") == '{"id":"owner","kind":"owner"}'
        before.pop("fields")
        before.pop("stage_ownership_overrides")
        before.pop("default_stage_ownership_mode")
        before.pop("alias")
        before.pop("backend_error")
        before.pop("at_cap")
        assert before == after
        blank = conn.execute("SELECT pending_proposal FROM tickets WHERE id='b'").fetchone()[0]
        assert json.loads(blank)["body"] == ""
        dropped = conn.execute(
            "SELECT pending_proposal, archived_field_content FROM tickets WHERE id='c'"
        ).fetchone()
        assert dropped[0] is None and "terminal draft" in dropped[1]
        rows = [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")]
        create_schema(conn)
        assert [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")] == rows
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("bad_case", ["shape", "awaiting", "workflow"])
def test_all_rows_are_validated_before_schema_or_text_changes(
    tmp_path: Path, bad_case: str
) -> None:
    path = tmp_path / "bad.db"
    _parent(path)
    _insert(path, "a", {"plan": {"proposal": _proposal("keep")}})
    _insert(
        path,
        "z",
        {"success": {"proposal": _proposal("offstage")}},
        status="awaiting_approval" if bad_case == "awaiting" else "empty",
        worker_type="unknown" if bad_case == "workflow" else "coding",
    )
    with connect(str(path)) as conn:
        if bad_case == "shape":
            conn.execute("UPDATE tickets SET fields='[]' WHERE id='z'")
        before = [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")]
        with pytest.raises(ValueError):
            create_schema(conn)
        assert [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")] == before
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "ticket_guidance"
        )
        assert "fields" in {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}


def test_late_write_failure_rolls_back_schema_and_earlier_conversions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rollback.db"
    _parent(path)
    for ticket_id in ("a", "b"):
        _insert(path, ticket_id, {"success": {"proposal": _proposal(ticket_id)}})
    original = db._migration_engine
    writes = 0

    def engine_with_failure(*args: Any, **kwargs: Any) -> Any:
        engine = original(*args, **kwargs)

        def before_write(
            conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
        ) -> None:
            nonlocal writes
            if statement.startswith("UPDATE tickets SET field_values="):
                writes += 1
                if writes == 2:
                    raise RuntimeError("late conversion failure")

        event.listen(engine, "before_cursor_execute", before_write)
        return engine

    monkeypatch.setattr(db, "_migration_engine", engine_with_failure)
    with connect(str(path)) as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")]
        with pytest.raises(RuntimeError, match="late conversion failure"):
            create_schema(conn)
        assert writes == 2
        assert [tuple(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")] == before
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "ticket_guidance"
        )
        assert "pending_proposal" not in {
            row["name"] for row in conn.execute("PRAGMA table_info(tickets)")
        }
