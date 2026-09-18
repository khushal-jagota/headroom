"""The ownership cutover keeps Tickets while removing per-Ticket policy."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from planner.core import db

SOURCE_REVISION = "remove_ticket_alias_and_backend_error"
TARGET_REVISION = "two_ownership_modes"
REMOVED_COLUMNS = {"stage_ownership_overrides", "default_stage_ownership_mode"}


def _upgrade(path: Path, revision: str) -> None:
    engine = db._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db._alembic_config(connection), revision)
    finally:
        engine.dispose()


def _table_structure(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        "columns": [
            (str(row[1]), str(row[2]), int(row[3]), row[4], int(row[5]))
            for row in conn.execute("PRAGMA table_info(tickets)")
        ],
        "foreign_keys": sorted(
            (str(row[2]), str(row[3]), str(row[4]), str(row[6]))
            for row in conn.execute("PRAGMA foreign_key_list(tickets)")
        ),
        "indexes": sorted(
            (str(row[1]), int(row[2]), str(row[3]), int(row[4]))
            for row in conn.execute("PRAGMA index_list(tickets)")
        ),
    }


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    worker_type: str,
    stage: str,
    ticket_status: str,
    override: str | None = None,
    default_ownership: str | None = None,
    assigned: int = 0,
) -> None:
    overrides = {} if override is None else {stage: override}
    conn.execute(
        "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
        "ticket_status,stage_ownership_overrides,default_stage_ownership_mode,field_values,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ticket_id,
            f"Ticket {ticket_id}",
            worker_type,
            "codex",
            stage,
            "done",
            ticket_status,
            json.dumps(overrides, separators=(",", ":")),
            default_ownership,
            "{}",
            1,
            2,
        ),
    )
    conn.execute(
        "INSERT INTO notification_attention_state"
        "(subject_kind,subject_id,notification_type,active,generation) "
        "VALUES ('ticket',?,'assigned',?,3)",
        (ticket_id, assigned),
    )


def test_cutover_removes_ticket_policy_and_reconciles_declared_ownership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ownership.db"
    _upgrade(path, SOURCE_REVISION)
    conn = db.connect(str(path))
    _insert_ticket(
        conn,
        "t_override_to_user",
        worker_type="coding",
        stage="needs_plan",
        ticket_status="empty",
        override="user",
        default_ownership="worker",
        assigned=1,
    )
    _insert_ticket(
        conn,
        "t_override_to_worker",
        worker_type="product_design",
        stage="needs_design",
        ticket_status="agent",
        override="worker",
        default_ownership="paired",
        assigned=0,
    )
    _insert_ticket(
        conn,
        "t_user_declared",
        worker_type="personal",
        stage="needs_outcome",
        ticket_status="awaiting_approval",
        assigned=0,
    )
    _insert_ticket(
        conn,
        "t_blocked",
        worker_type="research",
        stage="needs_research",
        ticket_status="blocked",
        override="user",
        assigned=1,
    )
    _insert_ticket(
        conn,
        "t_errored",
        worker_type="new_worker",
        stage="needs_understanding",
        ticket_status="errored",
        override="worker",
        assigned=0,
    )
    _insert_ticket(
        conn,
        "t_terminal",
        worker_type="coding",
        stage="done",
        ticket_status="empty",
        override="user",
        assigned=1,
    )
    conn.execute("INSERT INTO days(id,created_at,updated_at) VALUES ('day_test',1,2)")
    conn.execute(
        "INSERT INTO day_tickets(day_id,ticket_id,position) "
        "VALUES ('day_test','t_override_to_user',0)"
    )
    conn.execute(
        "INSERT INTO links(from_id,to_id,kind) VALUES ('t_override_to_user','t_blocked','blocks')"
    )
    conn.execute(
        "INSERT INTO ticket_paired_stage_openers(ticket_id,stage,opened_at) "
        "VALUES ('t_override_to_worker','needs_design',4)"
    )
    structure_before = _table_structure(conn)
    rows_before = {
        str(row["id"]): {
            key: value for key, value in dict(row).items() if key not in REMOVED_COLUMNS
        }
        for row in conn.execute("SELECT * FROM tickets")
    }
    conn.close()

    _upgrade(path, TARGET_REVISION)

    conn = db.connect(str(path))
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == TARGET_REVISION
    assert {
        str(row["id"]): dict(row) for row in conn.execute("SELECT * FROM tickets")
    } == rows_before
    structure_after = _table_structure(conn)
    assert [
        column for column in structure_before["columns"] if column[0] not in REMOVED_COLUMNS
    ] == (structure_after["columns"])
    assert structure_after["foreign_keys"] == structure_before["foreign_keys"]
    assert structure_after["indexes"] == structure_before["indexes"]
    assert conn.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM links").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM ticket_paired_stage_openers").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    ticket_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()[0]
    assert "stage_ownership_overrides" not in ticket_sql
    assert "default_stage_ownership_mode" not in ticket_sql
    assert (
        "ticket_status IN ('empty','blocked','agent','awaiting_approval','errored')" in ticket_sql
    )
    assert "'user'" not in ticket_sql
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE tickets SET ticket_status='user' WHERE id='t_terminal'")

    assigned = {
        str(row[0]): (int(row[1]), int(row[2]))
        for row in conn.execute(
            "SELECT subject_id,active,generation FROM notification_attention_state "
            "WHERE subject_kind='ticket' AND notification_type='assigned'"
        )
    }
    assert assigned == {
        "t_override_to_user": (0, 3),
        "t_override_to_worker": (1, 3),
        "t_user_declared": (1, 3),
        "t_blocked": (0, 3),
        "t_errored": (1, 3),
        "t_terminal": (0, 3),
    }
    conn.close()


def test_unknown_worker_stage_rolls_back_the_whole_cutover(tmp_path: Path) -> None:
    path = tmp_path / "unknown-stage.db"
    _upgrade(path, SOURCE_REVISION)
    conn = db.connect(str(path))
    _insert_ticket(
        conn,
        "t_unknown",
        worker_type="custom_worker",
        stage="needs_custom",
        ticket_status="empty",
    )
    before = [dict(row) for row in conn.execute("SELECT * FROM tickets")]
    conn.close()

    with pytest.raises(RuntimeError, match="undeclared Ticket Stage"):
        _upgrade(path, TARGET_REVISION)

    conn = db.connect(str(path))
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == SOURCE_REVISION
    assert [dict(row) for row in conn.execute("SELECT * FROM tickets")] == before
    assert REMOVED_COLUMNS.issubset(
        {str(row[1]) for row in conn.execute("PRAGMA table_info(tickets)")}
    )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
