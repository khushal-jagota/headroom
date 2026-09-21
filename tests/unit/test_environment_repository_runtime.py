from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from planner.environments.contracts import EnvironmentValidationError
from planner.environments.repository_runtime import (
    resolve_application_runtime_python,
    resolve_repository_runtime_python,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_repository_runtime_rejects_reused_interpreter_attached_elsewhere(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    interpreter = checkout / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(
        "#!/bin/sh\n"
        f'exec "{REPOSITORY_ROOT / ".venv" / "bin" / "python"}" "$@"\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    (checkout / "src" / "planner").mkdir(parents=True)
    (checkout / "src" / "planner" / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(EnvironmentValidationError, match="wrong checkout"):
        resolve_repository_runtime_python(checkout)


def test_application_runtime_accepts_release_root_with_its_own_interpreter(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    interpreter = release / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(
        "#!/bin/sh\n"
        f'PYTHONPATH="{release / "src"}" '
        f'exec "{REPOSITORY_ROOT / ".venv" / "bin" / "python"}" "$@"\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    (release / "src" / "planner").mkdir(parents=True)
    (release / "src" / "planner" / "__init__.py").write_text("", encoding="utf-8")
    assert resolve_application_runtime_python(release) == interpreter
