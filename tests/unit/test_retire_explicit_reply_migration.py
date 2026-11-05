"""Retiring the reply requirement leaves a stored marker's conversation as if never projected.

Risk: deleting the projected explicit_reply_missing rows strands the rest-line pointers
on missing rows, or moves them onto rows whose cached rest summary was cleared, so the
status line shows nothing. No other test seeds a database at the previous head with the
old projection. The focused staging-shape fixture reinstates the actual retired
table definitions on the fresh retained schema. It exercises both admitted migration starts without
restoration of the collapsed historical chain. The separate scratch proof uses a real
staging database. Events use the real projection writer and retain their stored bodies.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.support.migration_head import shipped_migration_head
from tests.unit.test_worker_memory import _make_conversation

from planner.conversation.events import (
    ConversationEventKind,
    conversation_event_payload_from_canonical_json,
)
from planner.conversation.storage import StoredConversationEvent
from planner.conversation.views.live_output import ConversationViewLiveOutput
from planner.conversation.views.status import read_status
from planner.conversation.views.writer import append_projection
from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)

OWNER = {"kind": "owner", "id": "owner"}
EVENTS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "prompt",
        {
            "sender_label": "owner",
            "content": [{"piece": "text", "text": "Please fix the login bug"}],
            "mode": "queue",
            "sender": OWNER,
        },
    ),
    ("agent_message", {"content": [{"piece": "text", "text": "Checking the session logs"}]}),
    ("explicit_reply_missing", {"prompt_sender": OWNER}),
    ("turn_ended", {"ending": "completed", "error_summary": None}),
)


# Exact retired table DDL from the isolated old-release snapshot at goal_holders.
STAGING_ONLY_TABLES = (
    (
        'CREATE TABLE worker_step_skill_bindings (\n\tsender_message_id TEXT NOT NULL, '
        '\n\tskill_role TEXT NOT NULL, \n\tskill_version_id TEXT NOT NULL, \n\tbinding_stat'
        "us TEXT DEFAULT 'provisional' NOT NULL, \n\tPRIMARY KEY (sender_message_id, sk"
        'ill_role), \n\tCONSTRAINT ck_worker_step_skill_binding_role CHECK (skill_role '
        "IN ('orientation', 'shared_worker', 'specialist')), \n\tCONSTRAINT ck_worker_s"
        "tep_skill_binding_status CHECK (binding_status IN ('provisional', 'final')),"
        ' \n\tFOREIGN KEY(skill_version_id) REFERENCES managed_skill_versions (id) ON D'
        'ELETE RESTRICT\n)'
    ),
    (
        'CREATE TABLE scheduled_ticket_occurrences (\n  schedule_id    TEXT NOT NULL R'
        'EFERENCES scheduled_ticket_schedules(id),\n  occurrence_key TEXT NOT NULL,\n  '
        'target_date  TEXT NOT NULL,\n  outcome        TEXT NOT NULL CHECK (outcome IN'
        " ('created', 'suppressed', 'failed')),\n  ticket_id      TEXT REFERENCES tick"
        'ets(id) ON DELETE SET NULL,\n  error          TEXT,\n  created_at     INTEGER '
        'NOT NULL,\n  PRIMARY KEY (schedule_id, occurrence_key)\n)'
    ),
    (
        'CREATE TABLE "scheduled_ticket_schedules" (manager_kind TEXT NOT NULL DEFAUL'
        "T 'chief' CHECK (manager_kind IN ('chief','manager')),id TEXT,enabled INTEGE"
        'R NOT NULL,cadence TEXT NOT NULL,local_time TEXT NOT NULL,title TEXT NOT NUL'
        "L,worker_type TEXT NOT NULL,kickoff_note TEXT DEFAULT '' NOT NULL,deadline T"
        'EXT,project_id TEXT,manager_id TEXT,employee_backend TEXT,employee_launch_mo'
        "del TEXT,blocked_by_ticket_ids TEXT DEFAULT '[]' NOT NULL,created_at INTEGER"
        " NOT NULL,updated_at INTEGER NOT NULL,locale TEXT NOT NULL DEFAULT 'vps' CHE"
        "CK (locale IN ('vps', 'macos')),PRIMARY KEY (id),CONSTRAINT ck_scheduled_tic"
        "ket_cadence CHECK (cadence IN ('daily','every_planning_day','current_sprint_"
        "day_four','current_sprint_final_day')),CHECK (enabled IN (0, 1)),FOREIGN KEY"
        '(manager_id) REFERENCES managers (id),FOREIGN KEY(project_id) REFERENCES pro'
        'jects (id),CHECK (length(title) <= 200))'
    ),
    (
        'CREATE TABLE feedback_notes (\n          id TEXT PRIMARY KEY,\n          text '
        'TEXT NOT NULL,\n          page_address TEXT,\n          page_label TEXT,\n     '
        "     state TEXT NOT NULL CHECK (state IN ('open', 'handled')),\n          tic"
        'ket_id TEXT REFERENCES tickets(id) ON DELETE SET NULL,\n          created_at '
        'INTEGER NOT NULL,\n          updated_at INTEGER NOT NULL,\n          handled_a'
        't INTEGER,\n          CHECK (\n            (page_address IS NULL AND page_labe'
        'l IS NULL) OR\n            (page_address IS NOT NULL AND substr(page_address,'
        " 1, 2) = '#/' AND\n             length(page_address) > 2 AND\n             pag"
        'e_label IS NOT NULL AND length(trim(page_label)) > 0)\n          ),\n         '
        " CHECK (\n            (state = 'open' AND ticket_id IS NULL AND handled_at IS"
        " NULL) OR\n            (state = 'handled' AND handled_at IS NOT NULL)\n       "
        '   )\n        )'
    ),
    (
        'CREATE TABLE navigation_commit_witnesses (sequence INTEGER PRIMARY KEY AUTOI'
        'NCREMENT, identity TEXT NOT NULL UNIQUE)'
    ),
    (
        'CREATE TABLE navigation_proof_identity (singleton INTEGER PRIMARY KEY CHECK('
        'singleton=1), secret TEXT NOT NULL)'
    ),
    (
        'CREATE TABLE conversation_reply_credits (\n        conversation_id TEXT NOT N'
        'ULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,\n        tur'
        'n_number INTEGER NOT NULL, principal_kind TEXT NOT NULL, principal_id TEXT N'
        'OT NULL,\n        PRIMARY KEY(conversation_id,turn_number,principal_kind,prin'
        'cipal_id))'
    ),
)


def _staging_shape(tmp_path: Path) -> Path:
    """Keep retained schema real and reinstate only the tables the upgrade removes."""
    path = tmp_path / "staging.db"
    with closing(connect(str(path))) as conn:
        create_schema(conn)
        for statement in STAGING_ONLY_TABLES:
            conn.execute(statement)
        # This is only an isolated unit fixture, not a database upgrade or proof stamp.
        conn.execute("UPDATE alembic_version SET version_num='goal_holders'")
        conn.commit()
    return path


def test_fresh_schema_has_no_reply_credits(tmp_path: Path) -> None:
    with closing(connect(str(tmp_path / "fresh.db"))) as conn:
        create_schema(conn)
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='conversation_reply_credits'"
        ).fetchone() is None
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            shipped_migration_head()
        )


def _projection(conn: sqlite3.Connection) -> tuple[list[tuple[Any, ...]], tuple[Any, ...]]:
    rows = [
        tuple(row) for row in conn.execute("SELECT * FROM conversation_view_rows ORDER BY sequence")
    ]
    state = conn.execute(
        "SELECT latest_full_happened_sequence,latest_focus_happened_sequence "
        "FROM conversation_view_state WHERE conversation_id='retired'"
    ).fetchone()
    return rows, tuple(state)


@pytest.mark.parametrize("entry", ["goal_holders", "retire_explicit_reply_requirement"])
def test_upgrade_deletes_marker_rows_and_moves_the_rest_line_to_what_remains(
    tmp_path: Path, entry: str,
) -> None:
    path = _staging_shape(tmp_path)
    with closing(connect(str(path))) as conn:
        _make_conversation(conn, "retired", compacted=0)
        for sequence, (kind, payload) in enumerate(EVENTS, start=1):
            encoded = json.dumps(payload)
            conn.execute(
                "INSERT INTO conversation_events VALUES ('retired',?,?,?,?)",
                (sequence, kind, encoded, 100 + sequence),
            )
            event_kind = ConversationEventKind(kind)
            append_projection(
                conn,
                [
                    StoredConversationEvent(
                        "retired",
                        sequence,
                        event_kind,
                        conversation_event_payload_from_canonical_json(event_kind, encoded),
                        100 + sequence,
                    )
                ],
            )
        never_projected = _projection(conn)
        # What the retired projection left: a focus-visible marker row in the turn that
        # both pointers name, with the rows they used to name stripped of their summary.
        conn.execute(
            "INSERT INTO conversation_view_rows(conversation_id,sequence,row_kind,created_at,"
            "changed_sequence,turn_key,focus_visible,rest_summary) "
            "VALUES ('retired',3,'explicit_reply_missing',103,3,'turn:e1',1,?)",
            (json.dumps({"kind": "explicit_reply_missing", "prompt_sender": OWNER}),),
        )
        conn.execute(
            "UPDATE conversation_view_state SET latest_full_happened_sequence=3,"
            "latest_focus_happened_sequence=3"
        )
        conn.execute("UPDATE conversation_view_rows SET rest_summary=NULL WHERE sequence<3")
        conn.commit()

        if entry == "retire_explicit_reply_requirement":
            with schema_migration_transaction(str(path)) as migration:
                upgrade_schema_connection(migration, entry)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == entry

        create_schema(conn)

        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            shipped_migration_head()
        )
        assert conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0] == len(EVENTS)
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='conversation_reply_credits'"
            ).fetchone()
            is None
        )
        assert _projection(conn) == never_projected
        assert [
            (row["sequence"], row["row_kind"])
            for row in conn.execute(
                "SELECT sequence,row_kind FROM conversation_view_rows ORDER BY sequence"
            )
        ] == [(1, "prompt"), (2, "agent_message"), (4, "turn_ended")]
        assert never_projected[1] == (2, 1)
        live = ConversationViewLiveOutput()
        assert read_status(conn, "retired", "full", live, running=False)["rest"][
            "latest_happened"
        ] == {"kind": "agent_message", "sender_label": None, "excerpt": "Checking the session logs"}
        assert read_status(conn, "retired", "focus", live, running=False)["rest"][
            "latest_happened"
        ] == {"kind": "prompt", "sender_label": "owner", "excerpt": "Please fix the login bug"}
