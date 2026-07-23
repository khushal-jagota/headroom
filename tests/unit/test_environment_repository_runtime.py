from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from planner.environments.contracts import EnvironmentValidationError
from planner.environments.repository_runtime import resolve_repository_runtime_python

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_repository_runtime_accepts_interpreter_attached_to_selected_checkout() -> None:
    assert resolve_repository_runtime_python(REPOSITORY_ROOT) == (
        REPOSITORY_ROOT / ".venv" / "bin" / "python"
    )


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


def test_repository_runtime_rejects_missing_checkout_interpreter(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    with pytest.raises(EnvironmentValidationError, match="missing or not executable"):
        resolve_repository_runtime_python(checkout)
