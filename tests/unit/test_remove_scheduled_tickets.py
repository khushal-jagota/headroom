"""A populated schedule migration must retain the Tickets it once supplied."""

from pathlib import Path

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.unit.test_db import _migration_tree_through

from planner.core.db import connect, create_schema
from planner.tickets.actions import create_ticket


def test_schedule_removal_preserves_ticket_and_reopens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = connect(str(tmp_path / "schedule-removal.db"))
    with monkeypatch.context() as historical:
        _migration_tree_through(tmp_path, historical, "manager_launch_defaults")
        create_schema(conn)
    ticket = create_ticket(
        conn,
        title="Existing work",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    conn.execute(
        "INSERT INTO scheduled_ticket_schedules "
        "(id,enabled,cadence,local_time,title,worker_type,kickoff_note,"
        "manager_kind,created_at,updated_at) "
        "VALUES ('disabled',0,'daily','09:00','Old timer','coding','Keep work','chief',1,1)"
    )
    conn.execute(
        "INSERT INTO scheduled_ticket_occurrences "
        "(schedule_id,occurrence_key,target_date,outcome,ticket_id,created_at) "
        "VALUES ('disabled','2026-10-16T09:00','2026-10-16','created',?,1)",
        (ticket.id,),
    )
    conn.commit()
    before = tuple(conn.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone())
    for _ in range(2):
        create_schema(conn)
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN "
            "('scheduled_ticket_schedules','scheduled_ticket_occurrences')"
        ).fetchall()
        assert (
            tuple(conn.execute("SELECT * FROM tickets WHERE id=?", (ticket.id,)).fetchone())
            == before
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
