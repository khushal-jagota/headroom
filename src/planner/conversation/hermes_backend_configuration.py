"""Resolve the Hermes ACP interpreter and Panels-owned Hermes home."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from planner.skill_sources import panels_skill_root

DEFAULT_HERMES_PYTHON: Final = "~/.hermes/hermes-agent/venv/bin/python"
DEFAULT_PLANNER_HOME: Final = "data/hermes-home"
PLANNER_SKILL_NAMES: Final = (
    "panels",
    "panels-ticket-management",
    "panels-worker",
    "panels-worker-coding",
    "panels-worker-new-worker",
    "panels-worker-exploration",
    "panels-worker-initiative-planning",
    "probe-worker",
    "panels-chief-of-staff",
    "panels-update-chief-of-staff",
    "panels-sprint-planning",
    "panels-rollover",
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
) -> None:
    """Expose packaged Panels skills and managed specialist skills in Hermes home."""
    from planner.worker_settings.service import materialize_specialist_skill
    from planner.worker_types.configuration import configured_worker_type_registry

    source_root = panels_skill_root()
    target_root = Path(home).expanduser() / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    registry = configured_worker_type_registry()
    specialist_skills = {
        registry.require(worker_type).worker_profile.specialist_skill: worker_type
        for worker_type in registry.registered_worker_types()
    }
    settings_parent = (
        Path(configured_database_parent).expanduser()
        if configured_database_parent is not None
        else Path(home).expanduser().parent
    )
    for skill_name in skill_names:
        source = source_root / skill_name
        if not source.is_dir():
            raise FileNotFoundError(f"planner skill not found: {source}")
        target = target_root / skill_name
        if skill_name in specialist_skills:
            if target.is_symlink():
                target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            materialize_specialist_skill(
                settings_parent, registry, specialist_skills[skill_name], target_root
            )
            continue
        if target.is_symlink():
            if target.resolve() == source.resolve():
                continue
            target.unlink()
        if target.exists():
            continue
        target.symlink_to(source, target_is_directory=True)
