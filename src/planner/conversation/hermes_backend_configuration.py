"""Resolve the Hermes ACP interpreter and Panels-owned Hermes home."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

DEFAULT_HERMES_PYTHON: Final = "~/.hermes/hermes-agent/venv/bin/python"
DEFAULT_PLANNER_HOME: Final = "data/hermes-home"
PLANNER_SKILL_NAMES: Final = (
    "panels",
    "panels-worker",
    "panels-worker-coding",
    "panels-worker-new-worker",
    "panels-worker-exploration",
    "panels-worker-initiative-planning",
    "probe-worker",
    "panels-chief-of-staff",
    "panels-sprint-planning",
    "panels-rollover",
)

ENV_HERMES_PYTHON: Final = "PLAN_HERMES_PYTHON"
ENV_PLANNER_HOME: Final = "PLAN_HERMES_HOME"


def resolve_hermes_python(
    value: str | None = None, env: Mapping[str, str] | None = None
) -> Path:
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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def provision_planner_home_skills(
    home: Path | str,
    skill_names: tuple[str, ...] = PLANNER_SKILL_NAMES,
) -> None:
    source_root = repo_root() / "skills"
    target_root = Path(home).expanduser() / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    for skill_name in skill_names:
        source = source_root / skill_name
        if not source.is_dir():
            raise FileNotFoundError(f"planner skill not found: {source}")
        target = target_root / skill_name
        if target.is_symlink():
            if target.resolve() == source.resolve():
                continue
            target.unlink()
        if target.exists():
            continue
        target.symlink_to(source, target_is_directory=True)
