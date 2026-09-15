"""The terminal proposal-delivery revision keeps the durable outbox intact."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.contracts import CHIEF_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.proposal_holder_wakes import data as wake_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, AtCap


def _upgrade_to(path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), revision)
    engine.dispose()


def test_upgrade_preserves_every_wake_field_state_foreign_key_and_due_index(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "proposal-delivery-failures.db"
    _upgrade_to(db_path, "work_attention")
    conn = connect(str(db_path))
    expected: dict[str, tuple[object, ...]] = {}
    states = ("pending", "delivering", "delivered", "uncertain", "cancelled")
    for position, state in enumerate(states, start=1):
        ticket = tickets_data.create_ticket(
            conn,
            title=f"Wake {state}",
            principal=CHIEF_PRINCIPAL,
            now=position,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
            kickoff_note="Work",
            stated_ceiling="needs_success",
            stated_at_cap=AtCap.propose,
        )
        wake_data.record_replacement(
            conn,
            ticket.id,
            holder=CHIEF_PRINCIPAL,
            message=f"Message {state}",
            now=100 + position,
        )
        conn.execute(
            "UPDATE proposal_holder_wakes SET proposal_generation=?,delivery_attempt=?,"
            "state=?,retry_at=?,last_error=?,created_at=?,updated_at=?,delivered_at=? "
            "WHERE ticket_id=?",
            (
                position + 2,
                position + 3,
                state,
                200 + position,
                f"error {state}",
                300 + position,
                400 + position,
                500 + position if state == "delivered" else None,
                ticket.id,
            ),
        )
        expected[ticket.id] = tuple(
            conn.execute(
                "SELECT * FROM proposal_holder_wakes WHERE ticket_id=?", (ticket.id,)
            ).fetchone()
        )
    conn.close()

    conn = connect(str(db_path))
    create_schema(conn)
    for ticket_id, values in expected.items():
        assert (
            tuple(
                conn.execute(
                    "SELECT * FROM proposal_holder_wakes WHERE ticket_id=?",
                    (ticket_id,),
                ).fetchone()
            )
            == values
        )
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND name='idx_proposal_holder_wakes_due'"
    ).fetchone()["sql"]
    assert "(state,retry_at,ticket_id)" in str(sql).replace(" ", "")

    pending_id = next(iter(expected))
    conn.execute("DELETE FROM tickets WHERE id=?", (pending_id,))
    assert (
        conn.execute(
            "SELECT 1 FROM proposal_holder_wakes WHERE ticket_id=?", (pending_id,)
        ).fetchone()
        is None
    )

    # The rebuilt constraint accepts the new terminal state.
    remaining_id = next(ticket_id for ticket_id in expected if ticket_id != pending_id)
    conn.execute(
        "UPDATE proposal_holder_wakes SET state='failed' WHERE ticket_id=?",
        (remaining_id,),
    )
    conn.close()
