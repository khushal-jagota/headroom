"""Keep conversation independent and executor peers behind their shared wire boundary."""

import ast
from importlib.util import resolve_name
from pathlib import Path


def test_conversation_and_executor_module_boundaries() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "planner"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        package = "planner." + ".".join(relative.parts[:-1])
        imports: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = resolve_name("." * node.level + (node.module or ""), package)
                imports.add(module)
                imports.update(f"{module}.{alias.name}" for alias in node.names)
        sides = {
            name.split(".")[2].split("_")[0]
            for name in imports
            if name.startswith(("planner.executor.mac_", "planner.executor.vps_"))
        }
        if relative.parts[0] == "conversation" and any(
            name == "planner.executor" or name.startswith("planner.executor.") for name in imports
        ):
            violations.append(f"{relative}: conversation imports executor")
        own_side = path.stem.split("_")[0]
        if own_side in {"mac", "vps"}:
            sides.add(own_side)
        if sides == {"mac", "vps"} and relative.as_posix() != "core/server.py":
            violations.append(f"{relative}: imports both executor sides")
    assert not violations, "\n".join(violations)
