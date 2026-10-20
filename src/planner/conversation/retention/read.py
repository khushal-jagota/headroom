"""Indexed facts about diagnostic source sequences that maintenance omitted."""

from __future__ import annotations

import sqlite3


def diagnostics_expired(conn: sqlite3.Connection, conversation_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM conversation_expired_ranges WHERE conversation_id=? LIMIT 1",
            (conversation_id,),
        ).fetchone()
        is not None
    )


def sequence_expired(conn: sqlite3.Connection, conversation_id: str, sequence: int) -> bool:
    # Ranges never overlap. Seek the closest preceding start with the primary key,
    # then test its end; scanning every earlier range grows with expired history.
    row = conn.execute(
        "SELECT last_sequence FROM conversation_expired_ranges "
        "WHERE conversation_id=? AND first_sequence<=? ORDER BY first_sequence DESC LIMIT 1",
        (conversation_id, sequence),
    ).fetchone()
    return row is not None and sequence <= row[0]
