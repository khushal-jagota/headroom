"""The proposal outbox removal discards only its obsolete delivery state."""

from __future__ import annotations

import json
from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.contracts import CHIEF_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


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
    )
    conn.execute(
        "INSERT INTO conversations "
        "(conversation_id,backend_key,workspace_folder,access,latest_sequence,created_at) "
        "VALUES ('c_old','codex','/tmp/work','full',6,1)"
    )
    conn.execute("UPDATE tickets SET conversation_id='c_old' WHERE id=?", (ticket.id,))
    prompt_sender_message_id = f"ticket-rejection:{ticket.id}:3:2:3"
    uncertain_sender_message_id = f"ticket-rejection:{ticket.id}:4:2:4"
    refused_sender_message_id = f"ticket-rejection:{ticket.id}:1:2:1"
    discarded_sender_message_id = f"ticket-rejection:{ticket.id}:2:2:2"
    conn.execute(
        "INSERT INTO conversation_events "
        "(conversation_id,sequence,kind,payload,created_at) VALUES "
        "('c_old',1,'proposal_delivery_failed','{}',2),"
        "('c_old',2,'turn_ended','{\"ending\":\"completed\",\"error_summary\":null}',3),"
        "('c_old',3,'prompt',?,4),"
        "('c_old',4,'prompt_delivery_uncertain',?,5),"
        "('c_old',5,'prompt_delivery_refused',?,6),"
        "('c_old',6,'prompt_discarded',?,7)",
        (
            json.dumps({"sender_message_id": prompt_sender_message_id}),
            json.dumps({"sender_message_id": uncertain_sender_message_id}),
            json.dumps({"sender_message_id": refused_sender_message_id}),
            json.dumps({"sender_message_id": discarded_sender_message_id}),
        ),
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
    legacy_comments = (
        ("pending", 1, "  Keep exact spacing.\nAnd this line.  ", "owner", "owner", True),
        ("delivering", 2, "Delivering without outcome", "sprint_item", "si_exact", True),
        ("delivering", 3, "Already reached by prompt", "chief", "chief", False),
        (
            "delivering",
            4,
            "Delivery outcome uncertain",
            "owner",
            "owner",
            False,
        ),
        ("uncertain", 5, "Uncertain state", "chief", "chief", False),
        ("delivered", 6, "Delivered state", "owner", "owner", False),
        ("cancelled", 7, "Cancelled state", "owner", "owner", False),
        ("pending", 8, "x" * 10_001, "owner", "owner", True),
    )
    for generation, (state, delivery_attempt, message, sender_kind, sender_id, _kept) in enumerate(
        legacy_comments, start=1
    ):
        conn.execute(
            "INSERT INTO ticket_rejection_messages "
            "(id,ticket_id,rejection_generation,sequence,delivery_attempt,message,"
            "sender_kind,sender_id,state,retry_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,2,?,?)",
            (
                f"comment-{generation}",
                ticket.id,
                generation,
                2,
                delivery_attempt,
                message,
                sender_kind,
                sender_id,
                state,
                generation + 2,
                generation + 2,
            ),
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
    assert "ticket_revision_feedback" in table_names
    feedback = upgraded.execute(
        "SELECT stage, feedback_json, revision FROM ticket_revision_feedback WHERE ticket_id=?",
        (ticket.id,),
    ).fetchone()
    assert feedback is not None
    assert str(feedback["stage"]) == tickets_data.read_ticket(upgraded, ticket.id).stage
    assert int(feedback["revision"]) == 1
    assert json.loads(str(feedback["feedback_json"])) == [
        {"sender_kind": sender_kind, "sender_id": sender_id, "message": message}
        for _state, _attempt, message, sender_kind, sender_id, kept in legacy_comments
        if kept
    ]
    assert [
        str(row["kind"])
        for row in upgraded.execute(
            "SELECT kind FROM conversation_events WHERE conversation_id='c_old' ORDER BY sequence"
        )
    ] == [
        "turn_ended",
        "prompt",
        "prompt_delivery_uncertain",
        "prompt_delivery_refused",
        "prompt_discarded",
    ]
    assert tickets_data.read_ticket(upgraded, ticket.id).title == "Keep the Ticket"
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
