"""Read exact per-conversation source and projection rows in one read-only snapshot."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any


def record_rows(path: str, identity: str) -> dict[str, Any]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")

        def rows(table: str, order: str) -> list[dict[str, Any]]:
            values = [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE conversation_id=? ORDER BY {order}", (identity,)
                )
            ]
            for row in values:
                for name in ("payload", "identity_environment_variables", "composer_catalog"):
                    if isinstance(row.get(name), str):
                        row[name] = json.loads(row[name])
            return values

        conversation = rows("conversations", "conversation_id")
        return {
            "conversation": conversation[0] if conversation else None,
            "events": rows("conversation_events", "sequence"),
            "lifecycle": rows(
                "conversation_lifecycle_facts",
                "kind,turn_number,operation,provider_identity,status,identity",
            ),
            "views": {
                "state": rows("conversation_view_state", "conversation_id"),
                "rows": rows("conversation_view_rows", "sequence"),
                "turns": rows("conversation_view_turns", "source_sequence,turn_key"),
                "groups": rows("conversation_view_groups", "source_sequence,group_key"),
                "lookups": rows(
                    "conversation_view_lookups", "lookup_kind,source_sequence,lookup_id"
                ),
            },
        }
