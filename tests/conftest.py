"""Refuse to run when the tests and the code under test come from different trees.

A virtualenv copied from another worktree points ``import planner`` at that other
worktree. The tests then pass or fail on somebody else's code while every path on
screen still reads correctly. This conftest runs inside the pytest process that is
under suspicion, so the answers it collects are the real ones.

The check lives in ``scripts/tree_environment.py`` so that ``./verify`` and mypy
state the same property. ``tests/unit/test_instrument.py`` reaches ``scripts`` the
same way.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE / "scripts"))

import tree_environment  # type: ignore[import-not-found]  # noqa: E402  # reached via sys.path.insert above; not visible to mypy


def pytest_configure(config: pytest.Config) -> None:
    found = tree_environment.faults(
        TREE, tree_environment.running_answers(TREE, ask_planner=True)
    )
    if found:
        raise pytest.UsageError(
            f"this environment does not belong to {TREE}:\n  "
            + "\n  ".join(found)
            + "\nRebuild it in place: rm -rf .venv && python3 -m venv .venv && "
            ".venv/bin/python -m pip install -r requirements.txt && "
            ".venv/bin/python -m pip install --editable ."
        )
