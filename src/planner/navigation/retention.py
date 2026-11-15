"""Prune complete journal prefixes and record their floor atomically."""

import sqlite3

from planner.navigation.configuration import DeliveryConfig


def prune(conn: sqlite3.Connection, config: DeliveryConfig) -> int:
    if not conn.in_transaction:
        raise RuntimeError("journal retention requires a writer transaction")
    epoch, current = conn.execute("SELECT epoch,revision FROM navigation_meta").fetchone()
    stored = conn.execute(
        "SELECT epoch,floor_revision FROM navigation_delivery_retention WHERE singleton=1"
    ).fetchone()
    floor = int(stored[1]) if stored is not None and stored[0] == epoch else 0
    count, size = conn.execute(
        "SELECT COUNT(*),COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM navigation_journal"
    ).fetchone()
    removed = 0
    # Only lengths are materialized; one indivisible giant body never fills the worker's heap.
    while count > config.retained_batch_count or size > config.retained_bytes:
        entries = conn.execute(
            "SELECT revision,length(CAST(body AS BLOB)) FROM navigation_journal "
            "WHERE revision>? ORDER BY revision LIMIT 256",
            (floor,),
        ).fetchall()
        if not entries:
            break
        for revision, body_bytes in entries:
            floor = int(revision)
            count -= 1
            size -= body_bytes
            removed += 1
            if count <= config.retained_batch_count and size <= config.retained_bytes:
                break
        conn.execute("DELETE FROM navigation_journal WHERE revision<=?", (floor,))
    # Even an entirely pruned journal preserves the exact last excluded revision.
    if not count:
        floor = max(floor, int(current))
    conn.execute(
        "INSERT INTO navigation_delivery_retention VALUES(1,?,?) "
        "ON CONFLICT(singleton) DO UPDATE SET epoch=excluded.epoch,"
        "floor_revision=excluded.floor_revision",
        (epoch, floor),
    )
    return removed
