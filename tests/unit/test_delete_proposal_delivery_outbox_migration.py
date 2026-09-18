"""The proposal outbox removal discards only its obsolete delivery state."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.contracts import CHIEF_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, AtCap


def test_upgrade_deletes_failure_events_and_drops_all_three_outbox_tables(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "delete-proposal-outbox.db"
    engine = db_module._migration_engine(str(db_path), 5000)  # noqa: SLF001
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(
                db_module._alembic_config(connection),  # noqa: SLF001
                "proposal_delivery_failures",
            )
    engine.dispose()

    conn = connect(str(db_path))
    ticket = tickets_data.create_ticket(
        conn,
        title="Keep the Ticket",
        principal=CHIEF_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Keep the work",
        stated_ceiling="needs_success",
        stated_at_cap=AtCap.propose,
    )
    conn.execute(
        "INSERT INTO conversations "
        "(conversation_id,backend_key,workspace_folder,access,latest_sequence,created_at) "
        "VALUES ('c_old','codex','/tmp/work','full',2,1)"
    )
    conn.execute(
        "INSERT INTO conversation_events "
        "(conversation_id,sequence,kind,payload,created_at) VALUES "
        "('c_old',1,'proposal_delivery_failed','{}',2),"
        "('c_old',2,'turn_ended','{\"ending\":\"completed\",\"error_summary\":null}',3)"
    )
    conn.execute(
        "INSERT INTO proposal_holder_wakes "
        "(ticket_id,proposal_generation,delivery_attempt,holder_kind,holder_id,message,"
        "state,retry_at,created_at,updated_at) "
        "VALUES (?,1,1,'chief','chief','Review','pending',2,2,2)",
        (ticket.id,),
    )
    conn.execute(
        "INSERT INTO ticket_rejection_messages "
        "(id,ticket_id,rejection_generation,sequence,delivery_attempt,message,state,"
        "retry_at,created_at,updated_at) "
        "VALUES ('rejection',?,1,1,1,'Returned','pending',2,2,2)",
        (ticket.id,),
    )
    conn.execute(
        "INSERT INTO proposal_delivery_failures "
        "(ticket_id,proposal_generation,attempt_count,last_error,visibility_message_id,"
        "created_at) VALUES (?,1,10,'failed','failure-visible',2)",
        (ticket.id,),
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)
    table_names = {
        str(row["name"])
        for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert (
        not {
            "proposal_holder_wakes",
            "ticket_rejection_messages",
            "proposal_delivery_failures",
        }
        & table_names
    )
    assert [
        str(row["kind"])
        for row in upgraded.execute(
            "SELECT kind FROM conversation_events WHERE conversation_id='c_old' ORDER BY sequence"
        )
    ] == ["turn_ended"]
    assert tickets_data.read_ticket(upgraded, ticket.id).title == "Keep the Ticket"
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
