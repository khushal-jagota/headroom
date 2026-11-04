"""Keep pytest and mypy on the source tree that they check."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def preflight(tree: Path) -> str | None:
    """Check the actual planner source path once, without importing its code."""
    source = tree.resolve() / "src"
    spec = importlib.util.find_spec("planner")
    origin = Path(spec.origin).resolve() if spec is not None and spec.origin else None
    if origin is None or not origin.is_relative_to(source):
        return (
            f"planner resolves to {origin}, outside {source}. "
            f"Use this worktree's environment and install {tree.resolve()} "
            "with pip install --editable ."
        )
    return None


def plugin(version: str) -> type:
    """Apply the same source path check before mypy analysis."""
    from mypy.plugin import Plugin

    diagnosis = preflight(Path(__file__).resolve().parent.parent)
    if diagnosis:
        print(f"mypy: {diagnosis}", file=sys.stderr)
        raise SystemExit(2)

    class TreeEnvironmentPlugin(Plugin):
        """The source path check is the entire plugin."""

    return TreeEnvironmentPlugin
