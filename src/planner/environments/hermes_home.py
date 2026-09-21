"""Where Hermes lives on this machine: its interpreter, its home, and the skills in it.

This is machine provisioning rather than conversation code. It says which Python runs
Hermes, which folder is its home, and puts Panels' own skills into that home. Everything
that asks — the backend that spawns Hermes, the worker settings screen, and the release
and backup tooling — asks the same question about the same machine, which is why it sits
with the rest of Panels' environment handling.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from planner.managed_skills import SKILL_FILE_NAME, managed_skills_home
from planner.skill_sources import RETIRED_PANELS_SKILL_NAMES, remove_retired_panels_skills

DEFAULT_HERMES_PYTHON: Final = "~/.hermes/hermes-agent/venv/bin/python"
DEFAULT_PLANNER_HOME: Final = "~/.hermes"
ENV_HERMES_PYTHON: Final = "PLAN_HERMES_PYTHON"
ENV_PLANNER_HOME: Final = "PLAN_HERMES_HOME"


def resolve_hermes_python(value: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    chosen = value or source.get(ENV_HERMES_PYTHON) or DEFAULT_HERMES_PYTHON
    return Path(chosen).expanduser()


def hermes_src_root(hermes_python: Path | str) -> Path:
    path = Path(hermes_python).expanduser()
    try:
        return path.parents[2]
    except IndexError:
        return path.parent


def resolve_planner_home(
    value: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    default: Path | str = DEFAULT_PLANNER_HOME,
) -> Path:
    source = env if env is not None else os.environ
    chosen = value or source.get(ENV_PLANNER_HOME) or default
    return Path(chosen).expanduser()


def provision_planner_home_skills(
    home: Path | str,
    *,
    configured_database_parent: Path | str | None = None,
) -> None:
    """Expose every managed Panels skill in a Hermes home.

    The home under ``data/skills`` is written from the database, so linking whatever is
    there is how a Worker type added to the database reaches the agent that runs it.
    """
    if configured_database_parent is None:
        raise ValueError("managed Panels skills require a database parent")
    source_root = managed_skills_home(configured_database_parent).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"managed Panels skills not found: {source_root}")
    target_root = Path(home).expanduser() / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    remove_retired_panels_skills(target_root)
    for source in sorted(source_root.iterdir(), key=lambda path: path.name):
        skill_name = source.name
        if skill_name in RETIRED_PANELS_SKILL_NAMES or skill_name.startswith("."):
            continue
        if not source.is_dir() or not (source / SKILL_FILE_NAME).is_file():
            continue
        target = target_root / skill_name
        if target.is_symlink():
            if target.resolve() == source.resolve():
                continue
            target.unlink()
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.symlink_to(source, target_is_directory=True)
