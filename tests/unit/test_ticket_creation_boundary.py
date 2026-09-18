from __future__ import annotations

import ast
from pathlib import Path


def test_production_ticket_creation_uses_the_canonical_action_boundary() -> None:
    source_root = Path(__file__).parents[2] / "src" / "planner"
    violations: list[str] = []

    for path in source_root.rglob("*.py"):
        relative = path.relative_to(source_root)
        if relative in {Path("tickets/actions.py"), Path("tickets/data.py")}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        data_aliases: set[str] = set()
        direct_names: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "planner.tickets.data"
            ):
                for imported in node.names:
                    if imported.name == "create_ticket":
                        direct_names.add(imported.asname or imported.name)
            if isinstance(node, ast.ImportFrom) and node.module == "planner.tickets":
                for imported in node.names:
                    if imported.name == "data":
                        data_aliases.add(imported.asname or imported.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in direct_names:
                violations.append(f"{relative}:{node.lineno}")
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_ticket"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in data_aliases
            ):
                violations.append(f"{relative}:{node.lineno}")

    assert violations == []
