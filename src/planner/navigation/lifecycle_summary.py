"""Compact raw lifecycle knowledge. No activity or capability inference occurs here."""

from __future__ import annotations

import sqlite3

from planner.navigation.contracts import LifecycleSummary, RawTurnValue

# Keep this exact predicate in the partial index and the count query. Unknown is
# terminal knowledge in v1 but still an uncertain outcome, including on old turns.
UNRESOLVED_PREDICATE = (
    "(kind='turn' AND status IN ('intent','running')) OR "
    "(kind='request' AND status IN ('pending','answering')) OR "
    "(kind='attempt' AND status IN ('intent','unknown'))"
)
LATEST_TURN_SQL = (
    "SELECT identity,turn_number,status FROM conversation_lifecycle_facts "
    "INDEXED BY navigation_lifecycle_latest_turn "
    "WHERE conversation_id=? AND kind='turn' "
    "ORDER BY turn_number DESC,identity DESC LIMIT 1"
)
UNRESOLVED_COUNTS_SQL = (
    "SELECT kind,status,COUNT(*) FROM conversation_lifecycle_facts "
    "INDEXED BY navigation_lifecycle_unresolved "
    "WHERE conversation_id=? AND (" + UNRESOLVED_PREDICATE + ") GROUP BY kind,status"
)
INDEXES = (
    "CREATE INDEX navigation_lifecycle_latest_turn ON conversation_lifecycle_facts "
    "(conversation_id,turn_number DESC,identity DESC,status) WHERE kind='turn'",
    "CREATE INDEX navigation_lifecycle_unresolved ON conversation_lifecycle_facts "
    "(conversation_id,kind,status) WHERE " + UNRESOLVED_PREDICATE,
)


def read_summary(conn: sqlite3.Connection, conversation_id: str) -> LifecycleSummary:
    """Read one latest raw turn and exact all-turn counts of six raw statuses.

    Settled history is excluded by the indexes. Count work remains proportional to
    unresolved rows. Nothing truncates uncertainty or infers that a process is alive.
    """
    row = conn.execute(LATEST_TURN_SQL, (conversation_id,)).fetchone()
    latest: RawTurnValue | None = None
    if row is not None:
        latest = {"identity": str(row[0]), "turn_number": int(row[1]), "status": str(row[2])}
    counts = {
        (str(row[0]), str(row[1])): int(row[2])
        for row in conn.execute(UNRESOLVED_COUNTS_SQL, (conversation_id,))
    }
    return {
        "latest_turn": latest,
        "turn_status_counts": {
            "intent": counts.get(("turn", "intent"), 0),
            "running": counts.get(("turn", "running"), 0),
        },
        "request_status_counts": {
            "pending": counts.get(("request", "pending"), 0),
            "answering": counts.get(("request", "answering"), 0),
        },
        "attempt_status_counts": {
            "intent": counts.get(("attempt", "intent"), 0),
            "unknown": counts.get(("attempt", "unknown"), 0),
        },
    }
