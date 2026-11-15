"""The upgrade repairs inflated boundaries without confusing idle and working compaction.

Fresh databases cannot expose this risk: a notice may already have answered an old
compaction before the old code advanced its boundary at turn end. Use the existing
migration fixture on a SQLite file, including a later idle compaction.
"""

from contextlib import closing
from pathlib import Path

from tests.unit.test_working_turn_compaction_boundary_migration import (
    ENDED,
    PANELS_COMPACT,
    WORK,
    _prompt,
    _seed,
)

from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.runtime.worker_memory import _working_turn_compaction_is_unanswered


def test_repairs_boundary_without_reopening_an_answered_working_compaction(tmp_path: Path) -> None:
    path = str(tmp_path / "previous.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, "merge_iphone_staging")
    with closing(connect(path)) as conn:
        with conn:
            _seed(
                conn,
                "c",
                7,
                (
                    (1, "prompt", WORK),
                    (2, "context_compacted", {}),
                    (
                        3,
                        "prompt",
                        _prompt("notice", "loop", sender_message_id="worker_step_memory_notice_1"),
                    ),
                    (4, "turn_ended", ENDED),
                    (5, "prompt", PANELS_COMPACT),
                    (6, "context_compacted", {}),
                    (7, "turn_ended", ENDED),
                ),
            )
            conn.execute(
                "UPDATE conversations SET working_turn_compacted_through_sequence=4 "
                "WHERE conversation_id='c'"
            )
        create_schema(conn)
        assert tuple(
            conn.execute(
                "SELECT automatically_compacted_through_sequence, "
                "working_turn_compacted_through_sequence, "
                "automatic_compaction_attempted_through_sequence FROM conversations "
                "WHERE conversation_id='c'"
            ).fetchone()
        ) == (6, 2, 7)
        assert not _working_turn_compaction_is_unanswered(conn, "c")
