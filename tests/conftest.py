"""Keep test imports on this source tree before test collection."""

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

diagnosis = tree_environment.preflight(TREE)
if diagnosis:
    raise pytest.UsageError(diagnosis)

from test_containment.admission import requested_containment  # noqa: E402
from test_containment.contracts import ContainmentRefusal  # noqa: E402

try:
    _BOUNDARY = requested_containment()
except ContainmentRefusal as error:
    raise pytest.UsageError(
        f"Test containment refused: {error}. Use scripts/run_contained_pytest.py."
    ) from error

# Default discovery omits the full installed build. Explicit paths opt in.
# Containment probes also stay out of ordinary discovery; their conftest refuses
# explicit execution outside a contained process.
collect_ignore = ["opt_in"] + (["containment"] if _BOUNDARY is None else [])

from tests.support.skill_tree_protection.pytest_plugin import bootstrap  # noqa: E402

_SKILL_TREE_PROTECTION = bootstrap(
    export_root=_BOUNDARY.scratch_root / "reports" if _BOUNDARY is not None else None
)


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
    os.environ["TMPDIR"] = str(Path(os.environ.get("TMPDIR", "/tmp")).resolve())
    tempfile.tempdir = None

    config.pluginmanager.register(_SKILL_TREE_PROTECTION, "skill-tree-protection")


@pytest.fixture(autouse=True)
def worker_process_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker launches never write custody into an inherited deployment directory."""
    monkeypatch.setenv("PLAN_WORKER_PROCESS_ROOT", str(tmp_path / "worker-processes"))
