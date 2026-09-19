"""The packaged seed skills, and exposing the skills home to a backend that reads files.

The managed home under ``data/skills`` is written from the database on every open. The
packaged tree here is only what a database with no skills in it is seeded from.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

from planner.managed_skills import managed_skills_home

SKILL_FILE_NAME = "SKILL.md"
SKILLS_DIR_NAME = "skills"
RETIRED_PANELS_SKILL_NAMES: Final = frozenset(
    {
        "panels-rollover",
        "panels-sprint-planning",
        "panels-ticket-management",
        "panels-update-chief-of-staff",
    }
)


def panels_skill_root() -> Path:
    """Return packaged defaults.  This tree is never a live authority."""
    return Path(__file__).resolve().parent / "skills"


def provision_native_backend_skills(
    native_home: Path | str,
    configured_database_parent: Path | str,
    *,
    packaged_skill_root: Path | str | None = None,
) -> Path:
    """Point one backend's native ``skills`` reader at the managed home."""
    del packaged_skill_root
    source_root = managed_skills_home(configured_database_parent).resolve()
    target = Path(native_home).expanduser() / SKILLS_DIR_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    panels_skill_names = {
        path.name
        for path in source_root.iterdir()
        if path.name not in RETIRED_PANELS_SKILL_NAMES
        and path.is_dir()
        and (path / SKILL_FILE_NAME).is_file()
    }
    if target.is_symlink():
        if target.resolve() == source_root:
            return source_root
        legacy_root = target.resolve(strict=False)
        target.unlink()
        target.mkdir()
        if legacy_root.is_dir():
            for legacy_skill in legacy_root.iterdir():
                if (
                    legacy_skill.name in panels_skill_names
                    or legacy_skill.name in RETIRED_PANELS_SKILL_NAMES
                ):
                    continue
                (target / legacy_skill.name).symlink_to(
                    legacy_skill, target_is_directory=legacy_skill.is_dir()
                )
    if not target.exists():
        target.symlink_to(source_root, target_is_directory=True)
        return source_root
    if not target.is_dir():
        raise FileExistsError(f"native backend skills path is not a directory: {target}")
    remove_retired_panels_skills(target)
    # Provider homes can already contain user-owned skills. Preserve them and
    # expose each missing Panels skill directly from the managed authority.
    for skill_name in panels_skill_names:
        source_skill = source_root / skill_name
        target_skill = target / source_skill.name
        if target_skill.exists() or target_skill.is_symlink():
            if target_skill.is_symlink() and target_skill.resolve() == source_skill:
                continue
            if target_skill.is_dir() and not target_skill.is_symlink():
                shutil.rmtree(target_skill)
            else:
                target_skill.unlink()
        target_skill.symlink_to(source_skill, target_is_directory=True)
    return source_root


def remove_retired_panels_skills(skill_root: Path | str) -> None:
    """Remove only explicitly retired Panels skill paths from one skills home."""
    root = Path(skill_root).expanduser()
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        _remove_path(root / retired_skill_name)


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
