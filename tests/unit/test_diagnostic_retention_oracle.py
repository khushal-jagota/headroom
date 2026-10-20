"""A raw-row oracle catches production selection errors without importing its classifier.

Risk: fixture survivors hide extra removals. These cheap SQLite cases independently
pin permitted sets, ownership conflicts, cutoff ties, pair ambiguity and control marks.
No server or provider connection is needed.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from planner.maintenance.diagnostic_history_measure import (
    fresh_backup,
    independent_removal_oracle,
    verify_removal,
)


@pytest.fixture
def archive() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE tickets(id,stage,worker_step_claim,conversation_id);
        CREATE TABLE sprint_items(id,settled);
        CREATE TABLE ticket_conversations(ticket_id,conversation_id);
        CREATE TABLE agents(agent_key,conversation_id);
        CREATE TABLE conversation_lifecycle_facts(conversation_id,kind,status);
        CREATE TABLE conversation_error_acknowledgements(conversation_id,through_sequence);
        CREATE TABLE conversations(conversation_id,identity_environment_variables,
            latest_sequence,owner_read_through_sequence,latest_agent_activity_sequence,
            automatically_compacted_through_sequence,automatic_compaction_attempted_through_sequence);
        CREATE TABLE conversation_events(conversation_id,sequence,kind,payload,created_at);
        INSERT INTO tickets VALUES('done','done','none','c');
    """)
    connection.execute(
        "INSERT INTO conversations VALUES(?,?,?,?,?,?,?)",
        (
            "c",
            json.dumps([["PLAN_ACTOR", "worker"], ["PLAN_TICKET_ID", "done"]]),
            9,
            0,
            0,
            0,
            0,
        ),
    )
    events = [
        (1, "prompt", {"text": "start"}, 1),
        (2, "agent_message", {"text": "old"}, 2),
        (3, "tool_call_started", {"tool_call_id": "x"}, 3),
        (4, "tool_call_finished", {"tool_call_id": "x"}, 4),
        (5, "tool_call_started", {"tool_call_id": "unmatched"}, 5),
        (6, "plan_updated", {"entries": []}, 6),
        (7, "plan_updated", {"entries": []}, 7),
        (8, "token_usage", {"input_tokens": 1}, 10),
        (9, "turn_ended", {"ending": "completed"}, 11),
    ]
    connection.executemany(
        "INSERT INTO conversation_events VALUES(?,?,?,?,?)",
        (("c", seq, kind, json.dumps(payload), created) for seq, kind, payload, created in events),
    )
    yield connection
    connection.close()


def test_oracle_exact_set_cutoff_latest_plan_and_unmatched_call(
    archive: sqlite3.Connection,
) -> None:
    assert independent_removal_oracle(archive, 10) == {("c", 2), ("c", 3), ("c", 4), ("c", 6)}
    archive.execute("UPDATE conversation_events SET created_at=11 WHERE sequence=2")
    assert independent_removal_oracle(archive, 10) == {("c", 3), ("c", 4), ("c", 6)}


def test_oracle_historical_unfinished_owner_holds_whole_conversation(
    archive: sqlite3.Connection,
) -> None:
    archive.execute("INSERT INTO tickets VALUES('unfinished','needs_implementation','none',NULL)")
    archive.execute("INSERT INTO ticket_conversations VALUES('unfinished','c')")
    assert independent_removal_oracle(archive, 10) == set()


def test_oracle_pair_member_marker_protects_both_and_duplicates_hold(
    archive: sqlite3.Connection,
) -> None:
    archive.execute("UPDATE conversations SET automatically_compacted_through_sequence=3")
    assert independent_removal_oracle(archive, 10) == {("c", 2), ("c", 6)}
    archive.execute("UPDATE conversations SET automatically_compacted_through_sequence=0")
    archive.execute(
        "INSERT INTO conversation_events VALUES(?,?,?,?,?)",
        (
            "c",
            10,
            "tool_call_finished",
            json.dumps({"tool_call_id": "x"}),
            5,
        ),
    )
    # This extra finish occurs after turn end, so it belongs to another turn.
    assert independent_removal_oracle(archive, 10) == {("c", 2), ("c", 3), ("c", 4), ("c", 6)}
    archive.execute("UPDATE conversation_events SET sequence=5 WHERE sequence=10")
    assert independent_removal_oracle(archive, 10) == {("c", 2), ("c", 6)}


def test_oracle_open_turn_and_unknown_identity_fail_closed(archive: sqlite3.Connection) -> None:
    archive.execute("DELETE FROM conversation_events WHERE kind='turn_ended'")
    assert independent_removal_oracle(archive, 10) == set()
    archive.execute(
        "UPDATE conversations SET identity_environment_variables=?",
        (json.dumps([["PLAN_ACTOR", "worker"], ["PLAN_TICKET_ID", "done"], ["future", "owner"]]),),
    )
    assert independent_removal_oracle(archive, 10) == set()


def test_backup_source_unchanged_and_proof_rejects_extra_deletion(tmp_path: Path) -> None:
    source, target = tmp_path / "source.db", tmp_path / "private.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE conversation_events(conversation_id,sequence,kind,payload,created_at)"
        )
        connection.executemany(
            "INSERT INTO conversation_events VALUES(?,?,?,?,?)",
            [
                ("c", 1, "prompt", "{}", 1),
                ("c", 2, "agent_message", "{}", 2),
            ],
        )
    before = source.read_bytes()
    fresh_backup(source, target)
    with sqlite3.connect(target) as connection:
        connection.execute("DELETE FROM conversation_events")
    assert source.read_bytes() == before
    with pytest.raises(ValueError, match="extra=1"):
        verify_removal(source, target, {("c", 2)})
    with pytest.raises(ValueError, match="new private path"):
        fresh_backup(source, target)


def test_unchanged_table_proof_detects_multiplicity_swap(tmp_path: Path) -> None:
    from contextlib import closing

    from planner.maintenance.diagnostic_history_measure import _same_table

    source, target = tmp_path / "before.db", tmp_path / "after.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE duplicate_rows(value)")
        connection.executemany("INSERT INTO duplicate_rows VALUES(?)", [("A",), ("A",), ("B",)])
        connection.commit()
    fresh_backup(source, target)
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("UPDATE duplicate_rows SET value='B' WHERE rowid=1")
        connection.commit()
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("ATTACH DATABASE ? AS after", (str(target),))
        assert not _same_table(connection, "duplicate_rows")
