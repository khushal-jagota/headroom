"""Shared helpers for the retained conversation tests: the isolated child environment,
database snapshot, and identity normalisation used by the unit15b, unit22 and worker
runtime tests."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
TABLES = (
    "conversations",
    "conversation_events",
    "conversation_lifecycle_facts",
    "conversation_view_state",
    "conversation_view_rows",
    "conversation_view_turns",
    "conversation_view_groups",
    "conversation_view_lookups",
)
ROW_ORDERS = {
    "conversation_lifecycle_facts": ("conversation_id,turn_number,kind,operation,identity"),
    "conversation_view_lookups": ("conversation_id,lookup_kind,source_sequence,lookup_id"),
}
RANDOM_ID = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}" r"-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])"
)


def environment(root: Path) -> dict[str, str]:
    home = root / "home"
    home.mkdir()
    runtime = root / "disconnected-runtime"
    runtime.mkdir(mode=0o700)
    return {
        "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
        "LANG": "C.UTF-8",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
        "HOME": str(home),
        "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "HERMES_HOME": str(home / "hermes"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/nonexistent-bus",
        "TMPDIR": str(root),
    }


def snapshot(path: str) -> dict[str, list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return {
            table: [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {ROW_ORDERS.get(table, '1,2')}"
                )
            ]
            for table in TABLES
        }


def normalize(value: Any) -> Any:
    """Map known UUID identities, never arbitrary hex content or digest fields."""
    identities: dict[str, str] = {}
    control_sources: dict[tuple[str, int, str], str] = {}
    control_operations = {"steer", "cancel", "permission_answer", "user_input_answer"}
    identity_fields = {
        "vendor_session_cursor",
        "provider_identity",
        "identity",
        "uuid",
        "session",
        "session_id",
        "ask_id",
        "request_id",
        "answer_id",
        "held_prompt_id",
        "cancelled",
        "still_queued",
    }
    json_fields = {"payload", "identity_environment_variables", "composer_catalog"}

    def collect(item: Any, field: str = "") -> None:
        if isinstance(item, dict):
            if (
                field == "conversation_lifecycle_facts"
                and item.get("kind") == "attempt"
                and item.get("operation") in control_operations
            ):
                raw_identity = item["identity"]
                assert re.fullmatch(r"[0-9a-f]{32}", raw_identity)
                control_key = (
                    item["conversation_id"],
                    item["turn_number"],
                    item["operation"],
                )
                assert control_sources.setdefault(control_key, raw_identity) == raw_identity
                identities[raw_identity] = "control:" + ":".join(map(str, control_key))
            for key in sorted(item):
                raw = item[key]
                if key in json_fields and isinstance(raw, str):
                    raw = json.loads(raw)
                collect(raw, key)
        elif isinstance(item, list):
            for child in item:
                collect(child, field)
        elif isinstance(item, str) and field in identity_fields:
            for match in RANDOM_ID.finditer(item):
                identities.setdefault(match[0], f"generated-{len(identities) + 1}")

    collect(value)

    def canonical_input(text: str) -> str:
        try:
            return json.dumps(
                json.loads(
                    text,
                    parse_float=lambda token: (
                        int(number) if (number := float(token)).is_integer() else number
                    ),
                ),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except json.JSONDecodeError:
            return text

    def walk(item: Any, field: str = "") -> Any:
        if isinstance(item, dict):
            result = {}
            for key in sorted(item):
                raw = item[key]
                if key in json_fields and isinstance(raw, str):
                    raw = json.loads(raw)
                    if key == "payload" and item.get("kind") in {
                        "tool_call_started",
                        "permission_asked",
                    }:
                        # Tool inputs keep JSON content, not Python's whitespace and key order.
                        if isinstance(raw.get("detail"), str):
                            raw["detail"] = canonical_input(raw["detail"])
                if key == "rest_summary" and isinstance(raw, str):
                    summary = json.loads(raw)
                    body = summary.get("body", {}).get("value")
                    if isinstance(body, dict) and body.get("kind") == "tool_call":
                        if isinstance(body.get("started_detail"), str):
                            body["started_detail"] = canonical_input(body["started_detail"])
                    raw = canonical_input(json.dumps(summary))
                result[key] = walk(raw, key)
            return result
        if isinstance(item, list):
            return [walk(child, field) for child in item]
        if isinstance(item, str):
            if "hash" in field or "digest" in field:
                return item
            for original, replacement in identities.items():
                item = item.replace(original, replacement)
            return item
        return item

    return walk(value)
