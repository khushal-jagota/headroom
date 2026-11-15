"""Stored compaction boundaries keep their mid-step notice across the upgrade.

Risk: the new working-turn column starts at zero for every existing row. A Worker whose
working turn was compacted before the deploy, and whose notice has not gone out, then
reads as answered. While its claim is out nothing promises it a next wake, so it is
stranded. Or the upgrade copies a boundary that Panels' own idle ``/compact`` set, and the
idle Worker is told to continue once more. Gap: the other tests start at the current
schema and cover only fresh boundaries. Layer: the real migrations on a SQLite file,
upgraded by ``create_schema`` like application startup, then the mid-step notice check.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from tests.support.migration_head import shipped_migration_head

from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.runtime.worker_memory import _working_turn_compaction_is_unanswered

PREVIOUS_HEAD = "recap_freshness"
ENDED: dict[str, object] = {"ending": "completed", "error_summary": None}


def _prompt(text: str, sender_label: str, **identity: object) -> dict[str, object]:
    return {"text": text, "sender_label": sender_label, "mode": "queue", **identity}


# A worker-step message from Panels: the kind of prompt a memory-loss note answers.
WORK = _prompt("Work this step", "Panels", sender_message_id="worker_step_message_a")
PANELS_COMPACT = _prompt("/compact", "Panels", system_generated=True)

# Each history is (stored boundary, events). The boundary is what the previous build
# wrote: the latest agent activity when the compaction was confirmed.
HISTORIES: dict[str, tuple[int, tuple[tuple[int, str, dict[str, object]], ...]]] = {
    # A long working turn compacted itself, and no notice has gone out yet.
    "working_turn": (
        3,
        ((1, "prompt", WORK), (2, "context_compacted", {}), (3, "turn_ended", ENDED)),
    ),
    # The turn ended at 2, and an hour later Panels' own /compact compacted the idle
    # conversation. Maintenance does not move the activity, so the boundary stays at 2.
    "idle_compact": (
        2,
        (
            (1, "prompt", WORK),
            (2, "turn_ended", ENDED),
            (3, "prompt", PANELS_COMPACT),
            (4, "context_compacted", {}),
            (5, "turn_ended", ENDED),
        ),
    ),
    # A working turn compacted, then Panels' own /compact ran and compacted nothing.
    "working_then_uncompacted_idle": (
        3,
        (
            (1, "prompt", WORK),
            (2, "context_compacted", {}),
            (3, "turn_ended", ENDED),
            (4, "prompt", PANELS_COMPACT),
            (5, "turn_ended", ENDED),
        ),
    ),
    # An idle compaction first, then a later working turn compacted again.
    "idle_then_working": (
        8,
        (
            (1, "prompt", WORK),
            (2, "turn_ended", ENDED),
            (3, "prompt", PANELS_COMPACT),
            (4, "context_compacted", {}),
            (5, "turn_ended", ENDED),
            (6, "prompt", WORK),
            (7, "context_compacted", {}),
            (8, "turn_ended", ENDED),
        ),
    ),
    "never_compacted": (0, ((1, "prompt", WORK), (2, "turn_ended", ENDED))),
}


def _seed(
    conn: sqlite3.Connection,
    conversation_id: str,
    boundary: int,
    events: tuple[tuple[int, str, dict[str, object]], ...],
) -> None:
    latest = events[-1][0]
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, access, "
        "created_at, latest_sequence, latest_agent_activity_at, latest_agent_activity_sequence, "
        "automatically_compacted_through_sequence, "
        "automatic_compaction_attempted_through_sequence) "
        "VALUES (?, 'claude', '/tmp', 'full', 0, ?, 0, ?, ?, ?)",
        (conversation_id, latest, latest, boundary, boundary),
    )
    for sequence, kind, payload in events:
        conn.execute(
            "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
            "created_at) VALUES (?, ?, ?, ?, 0)",
            (conversation_id, sequence, kind, json.dumps(payload)),
        )


def test_upgrade_keeps_working_turn_boundaries_and_drops_panels_own_compact(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "previous.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, PREVIOUS_HEAD)
    with closing(connect(path)) as conn:
        with conn:
            for conversation_id, (boundary, events) in HISTORIES.items():
                _seed(conn, conversation_id, boundary, events)

        create_schema(conn)

        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            shipped_migration_head()
        )
        boundaries = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT conversation_id, automatically_compacted_through_sequence, "
                "working_turn_compacted_through_sequence FROM conversations"
            )
        }
        owes_mid_step_notice = {
            conversation_id: _working_turn_compaction_is_unanswered(conn, conversation_id)
            for conversation_id in HISTORIES
        }
    assert boundaries == {
        "working_turn": (3, 3),
        "idle_compact": (2, 0),
        "working_then_uncompacted_idle": (3, 3),
        "idle_then_working": (8, 8),
        "never_compacted": (0, 0),
    }
    assert owes_mid_step_notice == {
        "working_turn": True,
        "idle_compact": False,
        "working_then_uncompacted_idle": True,
        "idle_then_working": True,
        "never_compacted": False,
    }
