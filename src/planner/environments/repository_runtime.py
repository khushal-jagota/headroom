"""Resolve the selected checkout's attached interpreter and planner source."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from planner.environments.contracts import EnvironmentValidationError


def resolve_repository_runtime_python(repository_root: Path) -> Path:
    repository = repository_root.resolve()
    interpreter = repository / ".venv" / "bin" / "python"
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise EnvironmentValidationError(
            f"repository runtime interpreter is missing or not executable: {interpreter}"
        )
    probe = subprocess.run(
        [
            str(interpreter),
            "-c",
            "import pathlib, planner; print(pathlib.Path(planner.__file__).resolve())",
        ],
        cwd=repository,
        env=_probe_environment(),
        capture_output=True,
        text=True,
        timeout=15.0,
        check=False,
    )
    if probe.returncode != 0:
        raise EnvironmentValidationError(
            "repository runtime could not import planner from its checkout: "
            f"{probe.stderr.strip()}"
        )
    imported_planner = Path(probe.stdout.strip()).resolve()
    expected_planner = (repository / "src" / "planner" / "__init__.py").resolve()
    if imported_planner != expected_planner:
        raise EnvironmentValidationError(
            "repository runtime imports planner from the wrong checkout: "
            f"expected {expected_planner}, got {imported_planner}"
        )
    return interpreter


def _probe_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in {"LANG", "LANGUAGE", "PATH", "TERM", "TMPDIR"} or key.startswith("LC_")
    }
