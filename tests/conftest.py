"""Refuse foreign environments and dependency drift before test collection.

A virtualenv copied from another worktree points ``import planner`` at that other
worktree. The tests then pass or fail on somebody else's code while every path on
screen still reads correctly. This conftest runs inside the pytest process that is
under suspicion, so the answers it collects are the real ones.

The check lives in ``scripts/tree_environment.py`` so that mypy states the same
property. ``tests/unit/test_instrument.py`` reaches ``scripts`` the same way.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE / "scripts"))

import tree_environment  # noqa: E402  # reached via sys.path.insert above


@pytest.fixture
def executor_transport_home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Retained fixture for synthetic executor test storage."""
    from tests.support.executor_https import short_transport_root

    from planner.executor.configuration import ExecutorPaths

    with short_transport_root() as root:
        monkeypatch.setattr(ExecutorPaths, "ipc_socket", property(lambda self: root / "ipc.sock"))
        yield root


def pytest_configure(config: pytest.Config) -> None:
    # File transport rejects symlink ancestors, including macOS's /var and /tmp.
    os.environ["TMPDIR"] = str(Path("/tmp").resolve())
    tempfile.tempdir = None
    diagnosis = tree_environment.preflight(TREE, ask_planner=True)
    if diagnosis:
        raise pytest.UsageError(diagnosis)
