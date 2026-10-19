"""Synthetic source records for the existing workspace browser hosts. No database or provider."""

import base64
import json
from typing import Any

from planner.navigation.contracts import SCHEMA_VERSION, SUPPORTED_SEMANTICS
from planner.navigation.delivery_contracts import WIRE_VERSION

# Authored host inputs are raw JSON objects. The browser validates the resulting
# wire payload through the production parser rather than a parallel fixture schema.
FixtureObject = dict[str, Any]

_navigation_revision = 0


def navigation_response(board: FixtureObject, workers: FixtureObject) -> FixtureObject:
    global _navigation_revision
    _navigation_revision += 1
    records: list[FixtureObject] = []
    summaries = {row["id"]: row for row in board.get("sprint_items", [])}
    cards = {row["id"]: row for column in board.get("columns", []) for row in column["cards"]}
    for item in summaries.values():
        for row in item.get("tickets", []):
            cards.setdefault(row["id"], row)
    conversations: dict[str, FixtureObject] = {}

    def conversation(row: FixtureObject) -> str | None:
        identity: str | None = row.get("conversation_id")
        if identity is None:
            return None
        bits = row.get(
            "conversation_signal",
            {"working": False, "message_for_owner": row.get("awaiting_reply", False)},
        )
        current = {
            "signal": dict(bits),
            "awaiting_answer": row.get("awaiting_answer", False),
            "last_turn_failed": row.get("agent_state") == "errored",
        }
        conversations[identity] = {
            "kind": "conversation",
            "id": identity,
            "value": {
                "latest_sequence": 20,
                "owner_read_through_sequence": 0,
                "latest_agent_activity_sequence": 20,
                "latest_agent_activity_at": None,
                "error_acknowledged_through_sequence": 0,
                "lifecycle_summary": {
                    "latest_turn": None,
                    "turn_status_counts": {"intent": 0, "running": 0},
                    "request_status_counts": {"pending": 0, "answering": 0},
                    "attempt_status_counts": {"intent": 0, "unknown": 0},
                },
                "attention": {
                    "current": current,
                    "durable": {
                        "message_for_owner": bits["message_for_owner"],
                        "last_turn_failed": current["last_turn_failed"],
                    },
                },
            },
        }
        return identity

    def address(
        kind: str, identity: str, conversation_id: str | None, parent_id: str | None = None
    ) -> None:
        if conversation_id is not None:
            records.append(
                {
                    "kind": "address",
                    "id": json.dumps([kind, identity, conversation_id]),
                    "value": {
                        "owner_kind": kind,
                        "owner_id": identity,
                        "conversation_id": conversation_id,
                        "current": True,
                        "parent_kind": ("chief" if parent_id == "chief" else "orchestrator")
                        if parent_id
                        else None,
                        "parent_id": parent_id,
                    },
                }
            )

    for row in cards.values():
        parent = row.get("sprint_item_id")
        parked = row.get("awaiting_approval", False) or row.get("awaiting_agent_approval", False)
        status = "awaiting_approval" if parked else row["ticket_status"]
        conv = conversation(row)
        records.append(
            {
                "kind": "ticket",
                "id": row["id"],
                "value": {
                    "title": row["title"],
                    "recap_preview": row.get("recap_preview", ""),
                    "recap": row.get("recap", row.get("recap_preview", "")),
                    "worker_type": row["worker_type"],
                    "stage": row["stage"],
                    "ticket_status": status,
                    "blocked": row.get("blocked", False),
                    "created_at": row.get("created_at", 1),
                    "updated_at": row.get("activity_at", 0),
                    "worker_step_claim_out": status == "agent",
                    "deadline": row.get("deadline"),
                    "project_id": row.get("project_id"),
                    "orchestrator_kind": "sprint_item" if parent else "chief",
                    "sprint_item_id": parent,
                    "conversation_id": conv,
                    "is_backlogged": row.get("is_backlogged", False),
                    "ceiling": "done",
                    "ceiling_holder": {"kind": "sprint_item", "id": parent or "fixture"}
                    if row.get("awaiting_agent_approval")
                    else {"kind": "owner", "id": "owner"},
                    "proposal_field": "implementation" if parked else None,
                    "proposal_revision": 1 if parked else 0,
                    "proposal_created_at": 1 if parked else None,
                    "stage_ownership": "user" if row.get("assigned") else "worker",
                    "terminal": row["stage"] == "done",
                    "locale": "en",
                    "gating_field": "consequences"
                    if row.get("waiting_to_closeout")
                    else row.get("gating_field", "implementation"),
                },
            }
        )
        address("ticket", row["id"], conv, parent or "chief")
        if row.get("project_id"):
            records.append({"kind": "project", "id": row["project_id"],
                            "value": {"name": row.get("project") or ""}})
    for row in summaries.values():
        conv = conversation(row)
        project_id = row.get("project_id") or "fixture-project"
        records.append(
            {
                "kind": "orchestrator",
                "id": row["id"],
                "value": {
                    "title": row["title"],
                    "recap_preview": row.get("recap_preview", ""),
                    "recap": row.get("recap", row.get("recap_preview", "")),
                    "created_at": row["created_at"],
                    "project_id": project_id,
                    "settled": row.get("settled", False),
                    "conversation_id": conv,
                },
            }
        )
        records.append(
            {
                "kind": "project",
                "id": project_id,
                "value": {"name": row.get("project") or ""},
            }
        )
        address("orchestrator", row["id"], conv)
    chief = workers["chief_of_staff"]
    conv = conversation(chief)
    records.append(
        {
            "kind": "chief",
            "id": "chief",
            "value": {"label": chief["label"], "conversation_id": conv},
        }
    )
    address("chief", "chief", conv)
    records.extend(conversations.values())
    unique = {(row["kind"], row["id"]): row for row in records}
    continuity = {
        "epoch": "fixture-navigation",
        "revision": str(_navigation_revision),
        "schema_version": SCHEMA_VERSION,
        "scope": "fixture-navigation-scope",
        "semantics": SUPPORTED_SEMANTICS,
    }
    cursor = (
        base64.urlsafe_b64encode(
            json.dumps(
                {**continuity, "wire_version": WIRE_VERSION}, sort_keys=True, separators=(",", ":")
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return {
        "wire_version": WIRE_VERSION,
        "mode": "current",
        "authority": {
            "delivery_session_id": "fixture-delivery",
            "availability": {"state": "current", "generation": "1", "reason": None},
            "permit": {
                "scope": "fixture-signal-scope",
                "runtime_epoch": "fixture-producer",
                "installation_id": "fixture-install",
                "generation": "1",
            },
        },
        "cursor": cursor,
        "snapshot": {**continuity, "records": list(unique.values())},
        "proof": None,
    }


def scoped_navigation_response(response: FixtureObject, view: FixtureObject) -> FixtureObject:
    """Reuse production membership/index logic over this host's synthetic source rows."""
    import sqlite3
    from typing import cast

    from planner.navigation.views.contracts import ViewRequest
    from planner.navigation.views.logic import digest
    from planner.navigation.views.ordering import ORDERING_GENERATION
    from planner.navigation.views.projection import rebuild
    from planner.navigation.views.read import identity, read_content, request
    from planner.navigation.views.schema import prepare

    descriptor = request(view)
    source = response["snapshot"]
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE navigation_meta(scope,schema_version,epoch,revision)")
        conn.execute("CREATE TABLE navigation_records(kind,id,value,PRIMARY KEY(kind,id))")
        conn.execute("CREATE TABLE navigation_dirty(kind,id)")
        conn.execute(
            "INSERT INTO navigation_meta VALUES(?,?,?,?)",
            (source["scope"], source["schema_version"], source["epoch"], int(source["revision"])),
        )
        conn.executemany(
            "INSERT INTO navigation_records VALUES(?,?,?)",
            [(row["kind"], row["id"], json.dumps(row["value"])) for row in source["records"]],
        )
        prepare(conn)
        rebuild(conn)
        coverage, content = read_content(conn, descriptor, current=response["mode"] == "current")
    return {
        "event": "snapshot",
        "wire_version": 3,
        "projection_version": 1,
        "view_id": identity(cast(ViewRequest, descriptor)),
        "scope_generation": descriptor["scope_generation"],
        "ordering_generation": ORDERING_GENERATION,
        "coverage": coverage,
        "content_version": digest(content),
        "mode": response["mode"],
        "authority": response["authority"],
        "content": content,
        "proof": response["proof"],
    }
