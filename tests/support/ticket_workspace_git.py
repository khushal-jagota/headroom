"""The existing local bare-Git fixture shared by workspace lifecycle checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

from planner.environments import ticket_scratch


def _git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout


def _layout(tmp_path: Path) -> ticket_scratch.ScratchLayout:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "staging", str(origin)], check=True)
    root = tmp_path / "panels"
    repository = root / "repository"
    subprocess.run(["git", "clone", "-q", str(origin), str(repository)], check=True)
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repository, "push", "-q", "origin", "HEAD:staging")
    _git(repository, "fetch", "-q", "origin")
    layout = ticket_scratch.ScratchLayout.under(root, home=tmp_path / "home")
    for folder in layout.folders:
        folder.mkdir(parents=True)
    return layout

