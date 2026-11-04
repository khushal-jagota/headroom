"""A holder restriction preserves existing work and its linked records."""

import shutil
import sqlite3
from pathlib import Path

import pytest
from tests.unit.test_db import _migration_tree_through

from planner.core import db
from planner.core.contracts import OWNER_PRINCIPAL
from planner.tickets import data


def _rows(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        name: sorted(map(tuple, conn.execute(f'SELECT * FROM "{name}"')), key=repr)
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT IN ('alembic_version','sqlite_sequence')"
        )
    }


def test_upgrade_keeps_work_children_indexes_and_triggers_and_narrows_holders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    # Test only this restriction and its ancestors. Later migrations can remove tables.
    tree = _migration_tree_through(tmp_path, monkeypatch, "remove_ticket_approvers")
    target = tree / "versions" / "remove_ticket_approvers.py"
    target.unlink()
    conn = db.connect(str(tmp_path / "upgrade.db"))
    db.create_schema(conn)
    parent = data.create_ticket(
        conn, title="Keep proposal and records", worker_type="coding", brief="Exact brief",
        principal=OWNER_PRINCIPAL, now=1, title_max_chars=200,
    )
    child = data.create_ticket(
        conn, title="Keep child", worker_type="coding", brief=None,
        principal=OWNER_PRINCIPAL, now=2, title_max_chars=200,
    )
    conn.execute(
        "INSERT INTO ticket_blocks (blocking_ticket_id,blocked_ticket_id) VALUES (?,?)",
        (parent.id, child.id),
    )
    conn.execute(
        "INSERT INTO ticket_revision_feedback "
        "(ticket_id,stage,feedback_json,revision,created_at,updated_at) VALUES (?,?,?,1,1,1)",
        (parent.id, parent.stage, '{"feedback":"Keep exact feedback"}'),
    )
    conn.commit()
    before = _rows(conn)
    objects_before = dict(conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type IN ('index','trigger') "
        "AND sql IS NOT NULL"
    ))
    shutil.copy(shipped / "versions" / target.name, target)
    db.create_schema(conn)
    assert _rows(conn) == before
    assert dict(conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type IN ('index','trigger') "
        "AND sql IS NOT NULL"
    )) == objects_before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    for kind, identity in (("owner", "owner"), ("chief", "chief"), ("manager", "si_kept")):
        conn.execute(
            "UPDATE tickets SET ceiling_holder=json_object('kind',?,'id',?) WHERE id=?",
            (kind, identity, parent.id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            "UPDATE tickets SET ceiling_holder=json_object('kind','ticket','id',?) WHERE id=?",
            (child.id, parent.id),
        )
    conn.close()
