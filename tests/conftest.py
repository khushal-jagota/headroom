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
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE / "scripts"))

import tree_environment  # noqa: E402  # reached via sys.path.insert above

# Dependency diagnosis must precede imports used by the guard (notably PyYAML).
# The planner import remains after guard installation in pytest_configure.
_BOOTSTRAP_DIAGNOSIS = tree_environment.preflight(TREE, ask_planner=False)
if _BOOTSTRAP_DIAGNOSIS:
    raise pytest.UsageError(_BOOTSTRAP_DIAGNOSIS)

from test_containment.admission import requested_containment  # noqa: E402
from test_containment.contracts import ContainmentRefusal  # noqa: E402

try:
    _BOUNDARY = requested_containment()
except ContainmentRefusal as error:
    raise pytest.UsageError(
        f"Test containment refused: {error}. Use scripts/run_contained_pytest.py."
    ) from error

# Default discovery must omit opt-in probes entirely: publication requires every
# selected file to execute tests. Explicit paths still load their refusal conftest.
collect_ignore = ["containment"] if _BOUNDARY is None else []

from tests.support.skill_tree_protection.pytest_plugin import bootstrap  # noqa: E402

_SKILL_TREE_PROTECTION = bootstrap(
    export_root=_BOUNDARY.scratch_root / "reports" if _BOUNDARY is not None else None
)


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(_SKILL_TREE_PROTECTION, "skill-tree-protection")
    diagnosis = tree_environment.preflight(TREE, ask_planner=True)
    if diagnosis:
        raise pytest.UsageError(diagnosis)
