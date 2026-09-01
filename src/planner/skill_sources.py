"""Manage the packaged seed skills and the durable Panels skills home."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

SKILL_FILE_NAME = "SKILL.md"
SKILLS_DIR_NAME = "skills"
LEGACY_WORKER_SETTINGS_DIR_NAME = "worker-settings"
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


def managed_panels_skill_root(configured_database_parent: Path | str) -> Path:
    return Path(configured_database_parent).expanduser() / SKILLS_DIR_NAME


def ensure_managed_panels_skills(
    configured_database_parent: Path | str,
    *,
    packaged_skill_root: Path | str | None = None,
) -> Path:
    """Seed missing managed skills once, preferring legacy in-product edits.

    Existing managed files are deliberately untouched: after first materialization,
    ``data/skills`` is the authority and package changes are only defaults for new
    homes.  A legacy worker-settings skill is copied only when its named managed
    target does not already exist.
    """
    target_root = managed_panels_skill_root(configured_database_parent)
    source_root = Path(packaged_skill_root or panels_skill_root()).expanduser()
    if not source_root.is_dir():
        raise FileNotFoundError(f"packaged Panels skills not found: {source_root}")
    target_root.mkdir(parents=True, exist_ok=True)
    remove_retired_panels_skills(target_root)
    legacy_by_name = _legacy_skills_by_name(
        Path(configured_database_parent).expanduser() / LEGACY_WORKER_SETTINGS_DIR_NAME
    )
    for source_dir in sorted(source_root.iterdir(), key=lambda path: path.name):
        if not source_dir.is_dir() or not (source_dir / SKILL_FILE_NAME).is_file():
            continue
        if source_dir.name in RETIRED_PANELS_SKILL_NAMES:
            continue
        target_dir = target_root / source_dir.name
        if target_dir.exists() or target_dir.is_symlink():
            continue
        legacy = legacy_by_name.get(source_dir.name)
        if legacy is not None:
            target_dir.mkdir(parents=True)
            shutil.copy2(legacy, target_dir / SKILL_FILE_NAME)
        else:
            shutil.copytree(source_dir, target_dir)
    return target_root


def provision_native_backend_skills(
    native_home: Path | str,
    configured_database_parent: Path | str,
    *,
    packaged_skill_root: Path | str | None = None,
) -> Path:
    """Point one backend's native ``skills`` reader at the managed home."""
    source_root = ensure_managed_panels_skills(
        configured_database_parent, packaged_skill_root=packaged_skill_root
    ).resolve()
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


def _legacy_skills_by_name(legacy_root: Path) -> dict[str, Path]:
    if not legacy_root.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in legacy_root.glob(f"*/{SKILL_FILE_NAME}"):
        name = _skill_name(path)
        if name is not None:
            found.setdefault(name, path)
    return found


def _skill_name(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "---":
            continue
        if line.startswith("name:"):
            return line.partition(":")[2].strip().strip("\"'") or None
    return None
