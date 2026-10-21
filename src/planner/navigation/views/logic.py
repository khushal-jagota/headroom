"""Pure bounded transport projection and existing workspace grouping policy."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from planner.navigation.contracts import NavigationRecord
from planner.navigation.views.configuration import DEFAULT_CONFIG, ViewConfig
from planner.navigation.views.contracts import ViewRecord

GROUPS = (
    "awaiting_approval",
    "awaiting_answer",
    "assigned",
    "awaiting_reply",
    "errored",
    "backlog",
    "agent",
    "waiting_to_closeout",
    "status_awaiting_approval",
    "empty",
    "blocked",
    "done",
)


def compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(compact(value).encode()).hexdigest()


def project(
    record: NavigationRecord,
    config: ViewConfig = DEFAULT_CONFIG,
    *,
    settled_only: bool = False,
) -> ViewRecord:
    value: dict[str, Any] = dict(record["value"])
    kind = record["kind"]
    if settled_only and kind == "manager" and value["settled"]:
        return cast(
            ViewRecord,
            {
                "kind": kind,
                "id": record["id"],
                "value": {
                    name: value[name]
                    for name in ("title", "created_at", "settled", "conversation_id")
                },
            },
        )
    if kind == "conversation":
        value = {
            name: value[name]
            for name in (
                "owner_read_through_sequence",
                "error_acknowledged_through_sequence",
                "attention",
            )
        }
    if kind in ("ticket", "manager"):
        recap = str(value["recap"])
        size = len(recap.encode())
        value["recap_reference"] = None
        if size > config.inline_recap_bytes:
            value["recap"] = value["recap_preview"]
            value["recap_reference"] = {
                "owner": {"kind": kind, "id": record["id"]},
                "version": hashlib.sha256(recap.encode()).hexdigest(),
                "utf8_bytes": size,
            }
    return cast(ViewRecord, {"kind": kind, "id": record["id"], "value": value})


def group(value: dict[str, Any], attention: dict[str, Any] | None, *, rail: bool) -> str:
    if value["is_backlogged"]:
        return "backlog"
    owner = value["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    parked = value["ticket_status"] == "awaiting_approval"
    if parked and owner:
        return "awaiting_approval"
    if attention and attention["awaiting_answer"]:
        return "awaiting_answer"
    if value["stage_ownership"] == "user":
        return "assigned"
    if not rail and attention and attention["signal"]["message_for_owner"]:
        return "awaiting_reply"
    working = bool(attention and attention["signal"]["working"])
    failed = bool(attention and attention["last_turn_failed"])
    if not working and (value["ticket_status"] == "errored" or failed and value["stage"] != "done"):
        return "errored"
    if value["ticket_status"] == "agent" or working:
        return "agent"
    if value["gating_field"] == "consequences" and value["ticket_status"] == "empty":
        return "waiting_to_closeout"
    if parked and not owner:
        return "status_awaiting_approval"
    if value["ticket_status"] == "blocked":
        return "blocked"
    if value["stage"] == "done":
        return "done"
    return "empty"


def section_key(
    section: str, owner: tuple[str, str] | None, group: str = "all", child_filter: str = "all"
) -> str:
    return compact([section, owner, group, child_filter])
