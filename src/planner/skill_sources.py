"""The packaged seed skills, and exposing the skills home to a backend that reads files.

The managed home under ``data/skills`` is written from the database on every open. The
packaged tree here is only what a database with no skills in it is seeded from.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from pathlib import Path
from typing import Final

from planner.managed_skills import managed_skills_home, skill_publication_lock

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

# These specialists remain readable for historical Tickets, but do not belong in
# every agent's discovery catalog. A Worker receives its assigned specialist.
WORKER_ONLY_SKILL_NAMES: Final = frozenset(
    {
        "panels-worker-personal-task",
        "panels-worker-planning-day",
        "panels-worker-planning-midday-check",
        "panels-worker-planning-sprint",
    }
)


def panels_skill_root() -> Path:
    """Return packaged defaults.  This tree is never a live authority."""
    return Path(__file__).resolve().parent / "skills"


def provision_workspace_skills(
    workspace_root: Path | str, configured_database_parent: Path | str
) -> Path:
    """Expose managed skills without replacing any workspace-owned entry.

    Directory descriptors keep exclusive creation within the checked real parent.
    A concurrent creator is accepted only when it creates the same allowed shape.
    """
    with skill_publication_lock(configured_database_parent):
        return _provision_workspace_skills(workspace_root, configured_database_parent)


def _provision_workspace_skills(
    workspace_root: Path | str, configured_database_parent: Path | str
) -> Path:
    managed_root = managed_skills_home(configured_database_parent).resolve(strict=True)
    projection_home = Path(configured_database_parent) / "agent-skill-home"
    provision_native_backend_skills(projection_home, configured_database_parent)
    source_root = (projection_home / SKILLS_DIR_NAME).resolve(strict=True)
    workspace = Path(workspace_root)
    parent = workspace / ".agents"
    target = parent / SKILLS_DIR_NAME

    def conflict(path: Path) -> FileExistsError:
        return FileExistsError(
            f"workspace skills conflict at {path}; expected {target} to link to {source_root} "
            "through a real .agents directory"
        )

    with_parent_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    workspace_fd = os.open(workspace, with_parent_flags)
    try:
        try:
            os.mkdir(".agents", dir_fd=workspace_fd)
        except FileExistsError:
            pass
        try:
            parent_fd = os.open(".agents", with_parent_flags, dir_fd=workspace_fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise conflict(parent) from exc
            raise
        try:
            try:
                os.symlink(source_root, SKILLS_DIR_NAME, dir_fd=parent_fd)
            except FileExistsError:
                # Never unlink or overwrite, even for a dangling or invalid symlink.
                existing = os.stat(SKILLS_DIR_NAME, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISLNK(existing.st_mode):
                    raise conflict(target) from None
                link = Path(os.readlink(SKILLS_DIR_NAME, dir_fd=parent_fd))
                try:
                    resolved = (link if link.is_absolute() else parent / link).resolve(strict=True)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise conflict(target) from exc
                if resolved == managed_root:
                    # Migrate only our former canonical link. Foreign workspace
                    # entries retain the same refusal and nonreplacement contract.
                    os.unlink(SKILLS_DIR_NAME, dir_fd=parent_fd)
                    os.symlink(source_root, SKILLS_DIR_NAME, dir_fd=parent_fd)
                elif resolved != source_root:
                    raise conflict(target) from None
        finally:
            os.close(parent_fd)
    finally:
        os.close(workspace_fd)
    return source_root


def provision_native_backend_skills(
    native_home: Path | str,
    configured_database_parent: Path | str,
    *,
    packaged_skill_root: Path | str | None = None,
    required_skill_names: tuple[str, ...] = (),
) -> Path:
    """Point one backend's native ``skills`` reader at the managed home."""
    del packaged_skill_root
    source_root = managed_skills_home(configured_database_parent).resolve()
    hidden_skills = RETIRED_PANELS_SKILL_NAMES | (
        WORKER_ONLY_SKILL_NAMES - set(required_skill_names)
    )
    target = Path(native_home).expanduser() / SKILLS_DIR_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    panels_skill_names = {
        path.name
        for path in source_root.iterdir()
        if path.name not in hidden_skills
        and path.is_dir()
        and (path / SKILL_FILE_NAME).is_file()
    }
    if target.is_symlink():
        legacy_root = target.resolve(strict=False)
        target.unlink()
        target.mkdir(exist_ok=True)
        if legacy_root.is_dir():
            for legacy_skill in legacy_root.iterdir():
                if (
                    legacy_skill.name in panels_skill_names
                    or legacy_skill.name in hidden_skills
                ):
                    continue
                (target / legacy_skill.name).symlink_to(
                    legacy_skill, target_is_directory=legacy_skill.is_dir()
                )
    if not target.exists():
        target.mkdir(exist_ok=True)
    if not target.is_dir():
        raise FileExistsError(f"native backend skills path is not a directory: {target}")
    for skill_name in hidden_skills:
        _remove_path(target / skill_name)
    # A deleted skill leaves its link behind. Remove only dead links into the managed
    # home; a provider home's own links, dead or alive, stay.
    for link in target.iterdir():
        if (
            link.is_symlink()
            and not link.exists()
            and (target / os.readlink(link)).parent.resolve() == source_root
        ):
            link.unlink()
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


def _remove_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
