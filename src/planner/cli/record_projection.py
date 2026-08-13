"""Shared projection and rendering for one Panels record read.

Domain APIs keep their rich detail responses.  CLI ``show`` commands map those
responses into this small contract so callers choose when authored parts expand.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypedDict


class RecordPart(TypedDict):
    value: Any
    user_note: Any
    proposal: Any


class ManifestEntry(TypedDict):
    character_count: int
    has_user_note: bool


def part(
    value: Any,
    *,
    user_note: Any = None,
    proposal: Any = None,
) -> RecordPart:
    """Build the common shape for one authored part."""
    return {"value": value, "user_note": user_note, "proposal": proposal}


def parse_part_names(raw: str | None) -> tuple[str, ...] | None:
    """Parse the optional, comma-separated positional part list."""
    if raw is None:
        return None
    names = tuple(name.strip() for name in raw.split(","))
    if not names or any(not name for name in names):
        raise ValueError("part names must be a comma-separated list")
    return names


def _character_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, ensure_ascii=False))


def project_record(
    header: Mapping[str, Any],
    parts: Mapping[str, RecordPart],
    requested: tuple[str, ...] | None,
) -> dict[str, Any]:
    """Return a manifest or the exact requested parts from one record."""
    if requested is None:
        manifest: dict[str, ManifestEntry] = {}
        for name, record_part in parts.items():
            manifest[name] = {
                "character_count": _character_count(record_part["value"]),
                "has_user_note": record_part["user_note"] is not None,
            }
        return {"header": dict(header), "manifest": manifest}

    duplicates = tuple(
        dict.fromkeys(name for name in requested if requested.count(name) > 1)
    )
    if duplicates:
        valid = ", ".join(parts)
        raise ValueError(
            f"duplicate part names: {', '.join(duplicates)}; valid part names: {valid}"
        )
    unknown = tuple(name for name in requested if name not in parts)
    if unknown:
        valid = ", ".join(parts)
        raise ValueError(
            f"unknown part names: {', '.join(unknown)}; valid part names: {valid}"
        )
    return {
        "header": dict(header),
        "parts": {name: parts[name] for name in requested},
    }


def _display(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _append_value(lines: list[str], label: str, value: Any, indent: str) -> None:
    rendered = _display(value)
    if "\n" not in rendered:
        lines.append(f"{indent}{label}: {rendered}")
        return
    lines.append(f"{indent}{label}: |")
    lines.extend(f"{indent}  {line}" for line in rendered.splitlines())


def render_text(projection: Mapping[str, Any]) -> str:
    """Render the JSON projection without changing its information hierarchy."""
    lines = ["header:"]
    for key, value in projection["header"].items():
        _append_value(lines, key, value, "  ")

    manifest = projection.get("manifest")
    if manifest is not None:
        lines.extend(("", "manifest:"))
        for name, entry in manifest.items():
            lines.append(f"  {name}:")
            lines.append(f"    character_count: {entry['character_count']}")
            lines.append(f"    has_user_note: {_display(entry['has_user_note'])}")
        return "\n".join(lines)

    lines.extend(("", "parts:"))
    for name, record_part in projection["parts"].items():
        lines.append(f"  {name}:")
        _append_value(lines, "value", record_part["value"], "    ")
        _append_value(lines, "user_note", record_part["user_note"], "    ")
        _append_value(lines, "proposal", record_part["proposal"], "    ")
    return "\n".join(lines)
