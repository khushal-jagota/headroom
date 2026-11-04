"""Read retained source evidence without a second message store."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import fields, replace

from planner.attention.contracts import AttentionEvent, Destination, Evidence, GateInterval
from planner.worker_settings.service import CHIEF_SETTINGS_KEY


def _snapshot(raw: str) -> Destination:
    value = json.loads(raw)
    names = {item.name for item in fields(Destination)}
    return Destination(**{key: item for key, item in value.items() if key in names})


def _destinations(conn: sqlite3.Connection) -> dict[tuple[str, str], Destination]:
    result: dict[tuple[str, str], Destination] = {}
    for row in conn.execute("SELECT id,name FROM projects"):
        identity = str(row["id"])
        result["project", identity] = Destination(
            "project", identity, str(row["name"]), None, project_id=identity
        )
    for row in conn.execute("SELECT id,title,project_id FROM managers"):
        identity = str(row["id"])
        result["manager", identity] = Destination(
            "manager",
            identity,
            str(row["title"]),
            f"/#/workspace/manager/{identity}",
            project_id=row["project_id"],
            manager_id=identity,
            attribution_basis="current_membership",
        )
    for row in conn.execute(
        "SELECT t.id,t.title,CASE WHEN t.manager_kind='manager' "
        "THEN m.project_id ELSE t.project_id END project_id,t.manager_id "
        "FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id"
    ):
        identity = str(row["id"])
        result["ticket", identity] = Destination(
            "ticket",
            identity,
            str(row["title"]),
            f"/#/workspace/{identity}",
            project_id=row["project_id"],
            manager_id=row["manager_id"],
            attribution_basis="current_membership",
        )
    for row in conn.execute("SELECT id,title,project_id FROM goals"):
        identity = str(row["id"])
        result["goal", identity] = Destination(
            "goal",
            identity,
            str(row["title"]),
            f"/#/workspace/goal/{identity}",
            project_id=row["project_id"],
            goal_id=identity,
            attribution_basis="current_membership",
        )
    result["chief", "chief"] = Destination("chief", "chief", "Chief", "/#/workspace/chief-of-staff")
    return result


def load_evidence(conn: sqlite3.Connection, *, first_at: int, last_at: int) -> Evidence:
    """Read identities before range filtering so replay cannot change attribution."""
    has_history = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attention_history_metadata'"
        ).fetchone()
        is not None
    )
    metadata = (
        conn.execute(
            "SELECT started_at FROM attention_history_metadata WHERE singleton=1"
        ).fetchone()
        if has_history
        else None
    )
    started_at = int(metadata["started_at"]) if metadata else last_at
    destinations = _destinations(conn)
    approval_rows = (
        conn.execute(
            "SELECT * FROM attention_approval_events WHERE occurred_at < ? ORDER BY occurred_at,id",
            (last_at,),
        ).fetchall()
        if has_history
        else []
    )
    gate_rows = (
        conn.execute(
            "SELECT * FROM attention_gate_intervals WHERE observed_since < ? "
            "AND (closed_at IS NULL OR closed_at > ?)",
            (last_at, first_at),
        ).fetchall()
        if has_history
        else []
    )
    message_destinations = dict(destinations)
    snapshots: dict[tuple[str, str], Destination] = {}
    for row in (*approval_rows, *gate_rows):
        snapshot_destination = _snapshot(str(row["destination_json"]))
        snapshots.setdefault(
            (snapshot_destination.kind, snapshot_destination.id), snapshot_destination
        )
    for key, snapshot in snapshots.items():
        destinations.setdefault(key, snapshot)
    by_conversation: dict[str, tuple[str, str]] = {}
    for row in conn.execute(
        "SELECT owner_kind,owner_id,conversation_id FROM conversation_history_catalog"
    ):
        kind = str(row["owner_kind"])
        if kind == "worker":
            kind = "ticket"
        elif kind == "sprint_item":
            kind = "manager"
        if kind in ("ticket", "manager", "goal", "chief") and row["owner_id"] is not None:
            by_conversation.setdefault(str(row["conversation_id"]), (kind, str(row["owner_id"])))
    for row in conn.execute(
        "SELECT conversation_id,ticket_id FROM ticket_conversations "
        "UNION SELECT conversation_id,id FROM tickets WHERE conversation_id IS NOT NULL"
    ):
        by_conversation[str(row["conversation_id"])] = "ticket", str(row["ticket_id"])
    for row in conn.execute(
        "SELECT agent_key,conversation_id FROM agents WHERE conversation_id IS NOT NULL"
    ):
        agent_key = str(row["agent_key"])
        identity: tuple[str, str] | None = None
        if agent_key == CHIEF_SETTINGS_KEY:
            identity = "chief", "chief"
        for prefix, kind in (
            ("sprint_item_supervisor_", "manager"),
            ("manager_", "manager"),
            ("goal_", "goal"),
        ):
            if agent_key.startswith(prefix):
                identity = kind, agent_key[len(prefix) :]
                break
        if identity is not None:
            by_conversation.setdefault(str(row["conversation_id"]), identity)
    events: list[AttentionEvent] = []
    missing_senders: list[int] = []
    # Earlier identities participate, so replay cannot move an old message into this window.
    rows = conn.execute(
        "SELECT conversation_id,sequence,kind,payload,created_at FROM conversation_events "
        "WHERE kind IN ('prompt','agent_message','turn_ended') AND created_at < ? "
        "ORDER BY created_at,rowid",
        (last_at,),
    )
    for row in rows:
        payload = json.loads(str(row["payload"]))
        recipient = payload.get("recipient")
        identity = by_conversation.get(str(row["conversation_id"]))
        if isinstance(recipient, dict) and recipient.get("kind") in (
            "ticket",
            "manager",
            "goal",
            "chief",
        ):
            identity = str(recipient["kind"]), str(recipient.get("id", ""))
        if identity is None:
            identity = "unattributed", "unattributed"
        destination = message_destinations.get(identity)
        if destination is None:
            snapshot_fallback = snapshots.get(identity)
            destination = (
                replace(
                    snapshot_fallback,
                    project_id=None,
                    manager_id=None,
                    goal_id=None,
                    attribution_basis="identity_only",
                )
                if snapshot_fallback is not None
                else Destination(
                    *identity,
                    "Deleted or unattributed work",
                    None,
                    attribution_basis="identity_only",
                )
            )
        kind = "activity"
        sender = payload.get("sender")
        if row["kind"] == "prompt":
            if isinstance(sender, dict) and sender == {"kind": "owner", "id": "owner"}:
                kind = "owner_message"
            elif (
                not isinstance(sender, dict) or "kind" not in sender or "id" not in sender
            ) and payload.get("system_generated") is not True:
                missing_senders.append(int(row["created_at"]))
        message_id = payload.get("sender_message_id")
        if (
            row["kind"] == "prompt"
            and isinstance(sender, dict)
            and isinstance(message_id, str)
            and message_id
        ):
            event_identity = "message:" + json.dumps(
                [sender.get("kind"), sender.get("id"), message_id], separators=(",", ":")
            )
        else:
            event_identity = f"conversation:{row['conversation_id']}:{row['sequence']}"
        events.append(AttentionEvent(event_identity, int(row["created_at"]), kind, destination))
    for row in approval_rows:
        owner = (
            row["actor_kind"] == "owner"
            and row["actor_id"] == "owner"
            and row["method"] == "explicit_accept"
        )
        events.append(
            AttentionEvent(
                f"approval:{row['id']}",
                int(row["occurred_at"]),
                "owner_approval" if owner else "activity",
                _snapshot(str(row["destination_json"])),
            )
        )
    gates = tuple(
        GateInterval(
            f"gate:{row['id']}",
            _snapshot(str(row["destination_json"])),
            row["opened_at"],
            int(row["observed_since"]),
            row["closed_at"],
        )
        for row in gate_rows
    )
    earliest = conn.execute("SELECT MIN(created_at) FROM conversations").fetchone()[0]
    return Evidence(
        tuple(events),
        gates,
        started_at,
        (
            "Gate waits are elapsed delays, not owner work hours. "
            "Reading time and Mac work are unobserved.",
            "Message attribution uses recorded recipients "
            "and labeled current relationship metadata.",
        ),
        tuple(destinations.values()),
        int(earliest) if earliest is not None else None,
        tuple(missing_senders),
    )
