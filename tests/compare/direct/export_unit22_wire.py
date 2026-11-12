"""Export the current Python executor wire without a server or database."""

from __future__ import annotations

import json
import sys
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

# Resolve this checkout, never a historical worktree or installed Panels package.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from planner.conversation.snapshot import BackendSnapshot  # noqa: E402
from planner.executor import protocol, protocol_schema, update_contracts  # noqa: E402
from planner.executor.contracts import ExecutorHealthRead  # noqa: E402


def export_wire() -> dict[str, Any]:
    roots = {
        name: value
        for name, value in vars(protocol).items()
        if isinstance(value, type) and is_dataclass(value)
    }
    roots.update(
        {
            name: value
            for name, value in vars(update_contracts).items()
            if isinstance(value, type) and is_dataclass(value)
        }
    )
    roots.update(BackendSnapshot=BackendSnapshot, ExecutorHealthRead=ExecutorHealthRead)
    definitions: dict[str, Any] = {}

    def define(name: str, schema: dict[str, Any]) -> None:
        if name in definitions and definitions[name] != schema:
            raise ValueError(f"Conflicting executor schema definition: {name}")
        definitions[name] = schema

    for name, value in sorted(roots.items()):
        schema = protocol_schema._without_documentation(
            protocol.type_adapter(value).json_schema()
        )
        assert isinstance(schema, dict)
        for nested_name, nested in schema.pop("$defs", {}).items():
            define(nested_name, nested)
        if schema != {"$ref": f"#/$defs/{name}"}:
            define(name, schema)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": dict(sorted(definitions.items())),
        "roots": sorted(roots),
        "manifest": protocol_schema.protocol_manifest(),
        "event_codecs": protocol_schema.protocol_schemas()["event_codecs"],
    }


if __name__ == "__main__":
    print(json.dumps(export_wire(), sort_keys=True, separators=(",", ":")))
