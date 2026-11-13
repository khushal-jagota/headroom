"""Upgrade preserves old undelivered guidance even after its stage no longer matches.

Prior feedback tests prove replacement only. This upgrade boundary proves that no
retained legacy text disappears and no exact proposal identity gets invented.
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from tests.support.principals import OWNER_PRINCIPAL

from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.tickets import data


def test_legacy_feedback_moves_to_durable_delivery_without_invented_proposal(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "legacy.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, "memory_notice_turns_are_maintenance")
    note = "  complete legacy\nUnicode café\nlast  "
    with closing(connect(path)) as conn:
        ticket = data.create_ticket(
            conn,
            title="Legacy",
            worker_type="coding",
            brief="Brief",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
        )
        conn.execute(
            "INSERT INTO ticket_revision_feedback "
            "(ticket_id,stage,feedback_json,revision,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (
                ticket.id,
                "a_previous_stage",
                json.dumps([{"sender_kind": "owner", "sender_id": "owner", "message": note}]),
                2,
                3,
                3,
            ),
        )
        conn.commit()
    with closing(connect(path)) as conn:
        create_schema(conn)
        row = conn.execute(
            "SELECT * FROM ticket_decision_messages WHERE ticket_id=?", (ticket.id,)
        ).fetchone()
        assert row["message"] == note
        assert row["proposal_revision"] is None
        assert row["delivery_status"] == "pending"
        assert (
            row["model_text"]
            == f"Legacy revision feedback from User for stage a_previous_stage:\n{note}"
        )
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='ticket_revision_feedback'"
            ).fetchone()
            is None
        )
