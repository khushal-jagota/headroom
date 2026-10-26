"""Refuse foreign environments and dependency drift before test collection.

A virtualenv copied from another worktree points ``import planner`` at that other
worktree. The tests then pass or fail on somebody else's code while every path on
screen still reads correctly. This conftest runs inside the pytest process that is
under suspicion, so the answers it collects are the real ones.

The check lives in ``scripts/tree_environment.py`` so that mypy states the same
property. ``tests/unit/test_instrument.py`` reaches ``scripts`` the same way.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE / "scripts"))

import tree_environment  # noqa: E402  # reached via sys.path.insert above


@pytest.fixture
def executor_transport_home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Use a short synthetic account home for real Unix socket tests."""
    from planner.executor import vps

    with tempfile.TemporaryDirectory(prefix="h-", dir="/tmp") as temporary:
        home = Path(temporary)
        monkeypatch.setattr(
            vps, "pwd", SimpleNamespace(getpwuid=lambda uid: SimpleNamespace(pw_dir=str(home)))
        )
        yield home


def pytest_configure(config: pytest.Config) -> None:
    diagnosis = tree_environment.preflight(TREE, ask_planner=True)
    if diagnosis:
        raise pytest.UsageError(diagnosis)
