"""Generate resource and bounded catalog declarations from canonical TypedDicts."""

from __future__ import annotations

import argparse
import importlib
import json
import types
from pathlib import Path
from typing import Literal, get_args, get_origin, get_type_hints, is_typeddict

TARGETS = {
    "planner.resource_versions.contracts": "web/src/lib/resourceVersions/contracts.ts",
    "planner.files.catalog_contracts": "web/src/lib/catalogs/artifacts.ts",
    "planner.conversation.history_contracts": "web/src/lib/catalogs/history.ts",
}


def generate(module_name: str = "planner.resource_versions.contracts") -> str:
    module = importlib.import_module(module_name)

    def type_name(value: object) -> str:
        if is_typeddict(value):
            return str(value.__name__)
        if get_origin(value) is list:
            return f"Array<{type_name(get_args(value)[0])}>"
        if get_origin(value) is types.UnionType:
            return " | ".join(type_name(part) for part in get_args(value))
        if get_origin(value) is Literal:
            return " | ".join(json.dumps(part) for part in get_args(value))
        return {str: "string", int: "number", object: "unknown", type(None): "null"}[value]

    declarations = [f"// Generated from {module_name}. Do not edit.\n"]
    for name, value in vars(module).items():
        if is_typeddict(value) and value.__module__ == module.__name__:
            fields = " ".join(
                f"{key}: {type_name(kind)};" for key, kind in get_type_hints(value).items()
            )
            declarations.append(f"export interface {name} {{ {fields} }}\n")
        elif get_origin(value) is Literal:
            declarations.append(f"export type {name} = {type_name(value)};\n")
    return "".join(declarations)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    check = parser.parse_args().check
    root = Path(__file__).resolve().parents[1]
    for module, target_path in TARGETS.items():
        target = root / target_path
        if check:
            if target.read_text() != generate(module):
                raise SystemExit(f"Resource contracts drifted. Regenerate {target_path}.")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(generate(module))
