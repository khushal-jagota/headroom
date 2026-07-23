"""Resolve the selected checkout's attached interpreter and planner source."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from planner.environments.contracts import EnvironmentValidationError


def resolve_application_runtime_python(runtime_root: Path) -> Path:
    runtime = runtime_root.resolve()
    interpreter = runtime / ".venv" / "bin" / "python"
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise EnvironmentValidationError(
            f"application runtime interpreter is missing or not executable: {interpreter}"
        )
    probe = subprocess.run(
        [
            str(interpreter),
            "-c",
            "import pathlib, planner; print(pathlib.Path(planner.__file__).resolve())",
        ],
        cwd=runtime,
        env=_probe_environment(),
        capture_output=True,
        text=True,
        timeout=15.0,
        check=False,
    )
    if probe.returncode != 0:
        raise EnvironmentValidationError(
            "application runtime could not import planner from its runtime root: "
            f"{probe.stderr.strip()}"
        )
    imported_planner = Path(probe.stdout.strip()).resolve()
    expected_planner = (runtime / "src" / "planner" / "__init__.py").resolve()
    if imported_planner != expected_planner:
        raise EnvironmentValidationError(
            "application runtime imports planner from the wrong checkout/runtime root: "
            f"expected {expected_planner}, got {imported_planner}"
        )
    return interpreter


def resolve_repository_runtime_python(repository_root: Path) -> Path:
    """Preserve the staging checkout contract while using the generic resolver."""
    return resolve_application_runtime_python(repository_root)


def _probe_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in {"LANG", "LANGUAGE", "PATH", "TERM", "TMPDIR"} or key.startswith("LC_")
    }
