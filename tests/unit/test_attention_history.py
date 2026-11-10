"""History proves canonical transitions, transaction rollback, and honest activation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic.script import ScriptDirectory

from planner.attention.history import capture_gate, install_history, record_approval
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.notifications.attention import capture_ticket_attention
from planner.tickets import data
from planner.tickets.contracts import NO_FURTHER, TicketEdit


def _ticket(conn: sqlite3.Connection, *, now: int = 100) -> str:
    return data.create_ticket(
        conn,
        title="Historical attention",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=200,
        brief="Agreed work",
        worker_type="coding",
    ).id


def _accept(conn: sqlite3.Connection, ticket_id: str, *, now: int = 200) -> None:
    data.accept_proposal(
        conn,
        ticket_id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )


def test_accept_records_actor_method_and_closes_once(tmp_db: sqlite3.Connection) -> None:
    ticket_id = _ticket(tmp_db)
    capture_ticket_attention(tmp_db, ticket_id, 120)
    _accept(tmp_db, ticket_id)
    capture_ticket_attention(tmp_db, ticket_id, 250)
    gates = tmp_db.execute("SELECT * FROM attention_gate_intervals").fetchall()
    approvals = tmp_db.execute("SELECT * FROM attention_approval_events").fetchall()
    assert len(gates) == 1
    assert (gates[0]["opened_at"], gates[0]["closed_at"]) == (100, 200)
    assert len(approvals) == 1
    assert (
        approvals[0]["actor_kind"],
        approvals[0]["actor_id"],
        approvals[0]["method"],
        approvals[0]["field"],
        approvals[0]["occurred_at"],
    ) == ("owner", "owner", "explicit_accept", "brief", 200)
    snapshot = json.loads(approvals[0]["destination_json"])
    assert snapshot["title"] == "Historical attention"
    assert snapshot["url"] == f"/#/workspace/{ticket_id}"


def test_delete_stops_wait_and_retains_snapshots(tmp_db: sqlite3.Connection) -> None:
    ticket_id = _ticket(tmp_db)
    record_approval(
        tmp_db,
        ticket_id=ticket_id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        method="explicit_accept",
        now=120,
    )
    data.delete_ticket(tmp_db, ticket_id, principal=OWNER_PRINCIPAL, now=200)
    row = tmp_db.execute("SELECT * FROM attention_gate_intervals").fetchone()
    assert row["closed_at"] == 200
    assert json.loads(row["destination_json"])["id"] == ticket_id
    assert tmp_db.execute("SELECT count(*) FROM attention_approval_events").fetchone()[0] == 1


def test_holder_transfer_and_revision_close_owner_gates(tmp_db: sqlite3.Connection) -> None:
    ticket_id = _ticket(tmp_db)
    chief = Principal(PrincipalKind.chief, "chief")
    data.edit_ticket(
        tmp_db,
        ticket_id,
        edit=TicketEdit(ceiling_holder=chief),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=130,
    )
    data.edit_ticket(
        tmp_db,
        ticket_id,
        edit=TicketEdit(ceiling_holder=OWNER_PRINCIPAL),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=160,
    )
    data.reject_proposal(
        tmp_db,
        ticket_id,
        message=None,
        principal=OWNER_PRINCIPAL,
        now=200,
    )
    rows = tmp_db.execute(
        "SELECT opened_at,closed_at FROM attention_gate_intervals ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [(100, 130), (160, 200)]
    assert tmp_db.execute("SELECT count(*) FROM attention_approval_events").fetchone()[0] == 0


def test_acceptance_history_rolls_back_with_source(tmp_db: sqlite3.Connection) -> None:
    ticket_id = _ticket(tmp_db)
    tmp_db.execute("BEGIN")
    _accept(tmp_db, ticket_id)
    tmp_db.rollback()
    row = tmp_db.execute("SELECT closed_at FROM attention_gate_intervals").fetchone()
    assert row[0] is None
    assert tmp_db.execute("SELECT count(*) FROM attention_approval_events").fetchone()[0] == 0
    assert data.read_ticket(tmp_db, ticket_id).pending_proposal is not None


def test_agent_acceptance_keeps_its_identity(tmp_db: sqlite3.Connection) -> None:
    ticket_id = _ticket(tmp_db)
    chief = Principal(PrincipalKind.chief, "chief")
    data.accept_proposal(
        tmp_db,
        ticket_id,
        field="brief",
        principal=chief,
        now=200,
        next_ceiling=NO_FURTHER,
        next_holder=chief,
    )
    row = tmp_db.execute(
        "SELECT actor_kind,actor_id,method FROM attention_approval_events"
    ).fetchone()
    assert tuple(row) == ("chief", "chief", "explicit_accept")


def test_activation_retains_unknown_opening_and_migration_has_one_head(
    tmp_db: sqlite3.Connection,
) -> None:
    ticket_id = _ticket(tmp_db)
    tmp_db.execute("DROP TABLE attention_approval_events")
    tmp_db.execute("DROP TABLE attention_gate_intervals")
    tmp_db.execute("DROP TABLE attention_history_metadata")
    # Capture is safe when earlier migrations run before these new tables exist.
    capture_gate(tmp_db, ticket_id=ticket_id, active=True, now=120)
    record_approval(
        tmp_db,
        ticket_id=ticket_id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        method="explicit_accept",
        now=120,
    )
    install_history(tmp_db, started_at=150)
    row = tmp_db.execute("SELECT * FROM attention_gate_intervals").fetchone()
    assert row["opened_at"] is None
    assert row["observed_since"] == 150
    _accept(tmp_db, ticket_id)
    assert tmp_db.execute("SELECT closed_at FROM attention_gate_intervals").fetchone()[0] == 200
    assert tmp_db.execute("SELECT started_at FROM attention_history_metadata").fetchone()[0] == 150
    script = ScriptDirectory(
        str(Path(__file__).resolve().parents[2] / "src/planner/core/migrations")
    )
    assert len(script.get_heads()) == 1


def test_automatic_acceptance_does_not_create_explicit_approval(
    tmp_db: sqlite3.Connection,
) -> None:
    ticket_id = _ticket(tmp_db)
    _accept(tmp_db, ticket_id)
    data.edit_ticket(
        tmp_db,
        ticket_id,
        edit=TicketEdit(ceiling="needs_consequences"),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=250,
    )
    data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Change the code",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=300,
    )
    assert tmp_db.execute("SELECT count(*) FROM attention_approval_events").fetchone()[0] == 1
