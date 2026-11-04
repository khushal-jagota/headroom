"""Frozen conversation payload for the original navigation migration only.

Current public reads reject schema 1. This helper preserves historical migration
backfill until the explicit v2 migration replaces records and journal continuity.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def lifecycle_payload(conn: sqlite3.Connection, conversation_id: str) -> list[dict[str, Any]]:
    columns = ("kind", "identity", "turn_number", "operation", "status", "provider_identity")
    return [
        dict(zip(columns, row, strict=True))
        for row in conn.execute(
            "SELECT kind,identity,turn_number,operation,status,provider_identity "
            "FROM conversation_lifecycle_facts WHERE conversation_id=? ORDER BY kind,identity",
            (conversation_id,),
        )
    ]
