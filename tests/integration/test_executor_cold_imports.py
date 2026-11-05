"""Cold process boundaries must work without pytest warming conversation imports."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def cold_python(program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", program],
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PLAN_") and key != "PYTHONPATH"
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


# Public process entrypoints exercise their transitive imports too.
@pytest.mark.parametrize(
    "module",
    [
        "planner.executor.mac_service",
        "planner.executor.vps_transport",
        "planner.cli.main",
    ],
)
def test_ordinary_entrypoint_imports_in_its_own_cold_process(module: str) -> None:
    result = cold_python(f"import importlib; importlib.import_module({module!r})")
    assert result.returncode == 0, result.stderr
