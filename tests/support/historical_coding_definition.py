"""Keep synthetic historical rows paired with their seven-stage definition.

The snapshot is the coding definition from e082b94d9ebc3e0170ff71ff3ea13304c23ee630,
before the fresh-install seed removed Success Condition and Plan. These migration
fixtures model that older definition, not a live amendment to the current type.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def seed_historical_coding_definition(conn: sqlite3.Connection) -> None:
    """Bind fixture metadata before its retained historical Tickets exist."""
    assert conn.execute(
        "SELECT 1 FROM tickets WHERE worker_type='coding' LIMIT 1"
    ).fetchone() is None, "historical definition setup must precede Ticket insertion"
    raw = Path(__file__).with_suffix(".json").read_text(encoding="utf-8")
    cursor = conn.execute(
        "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'", (raw,)
    )
    assert cursor.rowcount == 1, "historical definition setup requires the seeded coding type"
