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

from planner.skill_sources import (
    RETIRED_PANELS_SKILL_NAMES,
    ensure_managed_panels_skills,
    remove_retired_panels_skills,
)

DEFAULT_HERMES_PYTHON: Final = "~/.hermes/hermes-agent/venv/bin/python"
DEFAULT_PLANNER_HOME: Final = "~/.hermes"
PLANNER_SKILL_NAMES: Final = (
    "panels",
    "panels-worker",
    "panels-worker-coding",
    "panels-worker-debugging",
    "panels-worker-new-worker",
    "panels-worker-exploration",
    "panels-worker-initiative-planning",
    "panels-worker-product-design",
    "panels-worker-planning-day",
    "panels-worker-planning-midday-check",
    "panels-worker-planning-sprint",
    "probe-worker",
    "panels-chief-of-staff",
    "panels-update-chief-of-staff",
)

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
    skill_names: tuple[str, ...] = PLANNER_SKILL_NAMES,
    *,
    configured_database_parent: Path | str | None = None,
    panels_skills_source_root: Path | str | None = None,
) -> None:
    """Expose the managed Panels skills home in a Hermes home."""
    if configured_database_parent is None:
        raise ValueError("managed Panels skills require a database parent")
    source_root = ensure_managed_panels_skills(
        configured_database_parent, packaged_skill_root=panels_skills_source_root
    ).resolve()
    target_root = Path(home).expanduser() / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    remove_retired_panels_skills(target_root)
    for skill_name in skill_names:
        if skill_name in RETIRED_PANELS_SKILL_NAMES:
            continue
        source = source_root / skill_name
        if not source.is_dir():
            raise FileNotFoundError(f"planner skill not found: {source}")
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
