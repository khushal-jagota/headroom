"""Canonical address choice shared by the indexed and ordinary review readers."""

import json
import sqlite3
from typing import Any


def canonical_review_address(conn: sqlite3.Connection, conversation_id: str) -> str | None:
    row = conn.execute(
        "SELECT a.id FROM navigation_records a JOIN navigation_records owner "
        "ON owner.kind=json_extract(a.value,'$.owner_kind') "
        "AND owner.id=json_extract(a.value,'$.owner_id') "
        "WHERE a.kind='address' AND json_extract(a.value,'$.conversation_id')=? "
        "ORDER BY json_extract(a.value,'$.current') DESC,a.id LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return None if row is None else str(row[0])


def review_message_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for conversation_id, encoded in conn.execute(
        "SELECT id,value FROM navigation_records WHERE kind='conversation'"
    ):
        conversation = json.loads(encoded)
        if not conversation["attention"]["durable"]["message_for_owner"]:
            continue
        address_id = canonical_review_address(conn, conversation_id)
        if address_id is None:
            continue
        address = json.loads(
            conn.execute(
                "SELECT value FROM navigation_records WHERE kind='address' AND id=?", (address_id,)
            ).fetchone()[0]
        )
        owner = json.loads(
            conn.execute(
                "SELECT value FROM navigation_records WHERE kind=? AND id=?",
                (address["owner_kind"], address["owner_id"]),
            ).fetchone()[0]
        )
        items.append(
            {
                "review_item_type": "message",
                "conversation_id": conversation_id,
                "address_id": address_id,
                "owner_kind": address["owner_kind"],
                "owner_id": address["owner_id"],
                "title": owner.get("title", owner.get("label", "")),
                "waiting_since": conversation["first_unread_owner_message_at"],
                "recap": owner.get("recap", ""),
                "recap_reference": None,
            }
        )
    return items
