from __future__ import annotations

import json
from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect

PREVIOUS_REVISION = "weekly_sprint_checkpoint_schedule"
HEAD_REVISION = "ticket_conversation_history"


def _upgrade(db_path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(db_path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), revision)
    finally:
        engine.dispose()


def _conversation(conn: object, conversation_id: str, created_at: int) -> None:
    conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
        "access, created_at) VALUES (?, 'codex', 'model', '/work', 'full', ?)",
        (conversation_id, created_at),
    )


def _opener(ticket_id: str, title: str = "Known") -> str:
    return json.dumps(
        {
            "text": (
                f"Work ticket {ticket_id} — {title}. It is at Stage 'needs_success'; "
                "take the next step and propose the 'success' field for approval. "
                "Stage owner: worker."
            ),
            "sender_label": "loop",
            "mode": "run_when_free",
        },
        separators=(",", ":"),
    )


def _ticket(conn: object, ticket_id: str, title: str = "Known") -> str:
    conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, stage, ceiling, fields, "
        "created_at, updated_at) VALUES "
        "(?, ?, 'coding', 'hermes', 'needs_kickoff', 'needs_success', '{}', 1, 1)",
        (ticket_id, title),
    )
    return ticket_id


def test_migration_backfills_valid_unique_pointers_and_exact_worker_openers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.db"
    _upgrade(db_path, PREVIOUS_REVISION)
    conn = connect(str(db_path))
    try:
        tickets = [_ticket(conn, f"t_hist000{index}") for index in range(4)]
        active, duplicate_a, duplicate_b, recovered = tickets
        for index, conversation_id in enumerate(
            ("conv_active", "conv_duplicate", "conv_recovered", "conv_malformed", "conv_missing")
        ):
            _conversation(conn, conversation_id, index + 10)
        conn.execute(
            "UPDATE tickets SET conversation_id = 'conv_active' WHERE id = ?",
            (active,),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = 'conv_duplicate' "
            "WHERE id IN (?, ?)",
            (duplicate_a, duplicate_b),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = 'conv_absent' WHERE id = ?",
            (recovered,),
        )
        for conversation_id, payload in (
            ("conv_duplicate", _opener(duplicate_a)),
            ("conv_recovered", _opener(recovered)),
            ("conv_malformed", "{"),
            ("conv_missing", _opener("t_missing")),
        ):
            conn.execute(
                "INSERT INTO conversation_events "
                "(conversation_id, sequence, kind, payload, created_at) "
                "VALUES (?, 1, 'prompt', ?, 20)",
                (conversation_id, payload),
            )
    finally:
        conn.close()

    _upgrade(db_path, HEAD_REVISION)
    upgraded = connect(str(db_path))
    try:
        foreign_keys = upgraded.execute(
            "PRAGMA foreign_key_list(ticket_conversations)"
        )
        assert [str(row[2]) for row in foreign_keys] == [
            "tickets",
            "conversations",
        ]
        assert upgraded.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_ticket_conversations_ticket_id'"
        ).fetchone() is not None
        assert [
            tuple(row)
            for row in upgraded.execute(
                "SELECT conversation_id, ticket_id FROM ticket_conversations "
                "ORDER BY conversation_id"
            )
        ] == [
            ("conv_active", active),
            ("conv_recovered", recovered),
        ]
    finally:
        upgraded.close()


def test_migration_rejects_prompt_text_that_only_resembles_the_standard_opener(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "lookalike.db"
    _upgrade(db_path, PREVIOUS_REVISION)
    conn = connect(str(db_path))
    try:
        ticket_id = _ticket(conn, "t_look0000")
        _conversation(conn, "conv_lookalike", 10)
        payload = json.loads(_opener(ticket_id))
        payload["text"] += " Extra instructions."
        conn.execute(
            "INSERT INTO conversation_events "
            "(conversation_id, sequence, kind, payload, created_at) "
            "VALUES ('conv_lookalike', 1, 'prompt', ?, 20)",
            (json.dumps(payload),),
        )
    finally:
        conn.close()

    _upgrade(db_path, HEAD_REVISION)
    upgraded = connect(str(db_path))
    try:
        assert upgraded.execute("SELECT * FROM ticket_conversations").fetchone() is None
    finally:
        upgraded.close()
