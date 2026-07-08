"""Packaging contract for the local CLI name."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_panels_is_the_startup_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["panels"] == "planner.cli.main:main"
    assert "planner" not in scripts
