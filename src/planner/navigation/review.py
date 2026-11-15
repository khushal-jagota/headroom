"""Canonical address choice shared by the indexed and ordinary review readers."""

import json
import sqlite3
from typing import Any

from planner.navigation.review_logic import message_owner_is_live


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


def review_message_items(
    conn: sqlite3.Connection, *, ticket_id: str | None = None
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    address_order: dict[str, tuple[int, str, str]] = {}
    candidates = conn.execute(
        "SELECT id,value FROM navigation_records WHERE kind='conversation'"
        if ticket_id is None
        else "SELECT c.id,c.value FROM navigation_records c WHERE c.kind='conversation' "
        "AND EXISTS(SELECT 1 FROM navigation_records a WHERE a.kind='address' "
        "AND json_extract(a.value,'$.conversation_id')=c.id "
        "AND json_extract(a.value,'$.owner_kind')='ticket' "
        "AND json_extract(a.value,'$.owner_id')=?)",
        () if ticket_id is None else (ticket_id,),
    )
    for conversation_id, encoded in candidates:
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
        if ticket_id is not None and (
            address["owner_kind"] != "ticket" or address["owner_id"] != ticket_id
        ):
            continue
        owner = json.loads(
            conn.execute(
                "SELECT value FROM navigation_records WHERE kind=? AND id=?",
                (address["owner_kind"], address["owner_id"]),
            ).fetchone()[0]
        )
        if not message_owner_is_live(address["owner_kind"], owner):
            continue
        address_order[address_id] = (-int(address["current"]), address_id, conversation_id)
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
    return sorted(items, key=lambda item: address_order[item["address_id"]])
