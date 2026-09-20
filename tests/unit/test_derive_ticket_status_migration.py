"""The revision that stops storing a Ticket's status and keeps only the claim."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from tests.support.historical_tickets import insert_historical_ticket

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "drop_ticket_archived_field_content"
HEAD_REVISION = "derive_ticket_status"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)  # noqa: SLF001
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)  # noqa: SLF001
    finally:
        engine.dispose()
    return connect(str(path))


def _seed(conn: sqlite3.Connection) -> None:
    insert_historical_ticket(conn, "t_resting", title="Resting", ticket_status="empty")
    insert_historical_ticket(conn, "t_out", title="Step out", ticket_status="agent")
    insert_historical_ticket(conn, "t_failed", title="Failed step", ticket_status="errored")
    insert_historical_ticket(conn, "t_parked", title="Parked", ticket_status="awaiting_approval")
    conn.execute(
        "UPDATE tickets SET pending_proposal = ? WHERE id = 't_parked'",
        ('{"field":"success","body":"draft","proposed_by":"worker","created_at":5}',),
    )
    conn.execute(
        "UPDATE tickets SET ticket_status_changed_at = 4242, ticket_status_revision = 7 "
        "WHERE id = 't_out'"
    )


def test_the_claim_replaces_the_status_and_carries_its_freshness(tmp_path: Path) -> None:
    db_path = tmp_path / "derive-status.db"
    conn = _upgrade_to_previous_revision(db_path)
    _seed(conn)
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert (
        upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
    )
    claims = {
        str(row["id"]): str(row["worker_step_claim"])
        for row in upgraded.execute("SELECT id, worker_step_claim FROM tickets")
    }
    assert claims == {
        "t_resting": "none",
        "t_out": "out",
        "t_failed": "errored",
        # A parked proposal was never a claim. What it means is derived from the proposal.
        "t_parked": "none",
    }
    carried = upgraded.execute(
        "SELECT worker_step_claim_changed_at, worker_step_claim_revision FROM tickets "
        "WHERE id = 't_out'"
    ).fetchone()
    assert tuple(carried) == (4242, 7)

    columns = {str(row["name"]) for row in upgraded.execute("PRAGMA table_info(tickets)")}
    assert columns.isdisjoint(
        {"ticket_status", "ticket_status_changed_at", "ticket_status_revision"}
    )
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute("UPDATE tickets SET worker_step_claim = 'mystery' WHERE id = 't_out'")
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()


def test_every_ticket_whose_stored_status_disagreed_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The report is the point of running this once: those rows are the drift."""
    db_path = tmp_path / "disagreements.db"
    conn = _upgrade_to_previous_revision(db_path)
    _seed(conn)
    # Stored `empty` with a proposal parked on it: the status says resting, the facts say
    # a proposal is waiting for the owner. This is the shape the live database holds.
    insert_historical_ticket(conn, "t_drifted", title="Drifted", ticket_status="empty")
    conn.execute(
        "UPDATE tickets SET pending_proposal = ? WHERE id = 't_drifted'",
        ('{"field":"success","body":"draft","proposed_by":"worker","created_at":5}',),
    )
    # Stored `empty` with a live blocker: the stand-in was never re-derived.
    insert_historical_ticket(conn, "t_blocker", title="Blocker", stage="needs_implementation")
    insert_historical_ticket(conn, "t_blocked", title="Blocked", ticket_status="empty")
    conn.execute("INSERT INTO ticket_blocks VALUES ('t_blocker', 't_blocked')")
    conn.close()

    upgraded = connect(str(db_path))
    with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
        create_schema(upgraded)

    reported = {
        line.split()[1]: line.split()[2:]
        for line in caplog.messages
        if line.startswith("derive_ticket_status: t_")
    }
    assert reported == {
        "t_drifted": ["stored=empty", "derived=awaiting_approval"],
        "t_blocked": ["stored=empty", "derived=blocked"],
    }
    upgraded.close()
