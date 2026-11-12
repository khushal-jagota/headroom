"""The packaged seed skills, and exposing the skills home to a backend that reads files.

The managed home under ``data/skills`` is written from the database on every open. The
packaged tree here is only what a database with no skills in it is seeded from.
"""

from __future__ import annotations

import errno
import fcntl
import os
import shutil
import stat
from collections.abc import Mapping
from contextlib import ExitStack
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


def account_skill_homes(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Use the owner's configured provider homes without moving authentication."""
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME", str(Path.home()))).expanduser()
    return tuple(
        Path(env.get(key) or str(home / default)).expanduser()
        for key, default in (
            ("PLAN_HERMES_HOME", ".hermes"),
            ("CODEX_HOME", ".codex"),
            ("CLAUDE_CONFIG_DIR", ".claude"),
        )
    )


def _account_skill_conflict(path: Path, owner: str) -> str:
    return (
        "Panels supports one instance per OS account. Account skill provisioning was skipped: "
        f"{path} belongs to {owner}. Existing provider skills were preserved. "
        "The server can start, but this home cannot own its Worker skills. "
        "Use a separate OS account for another Panels home."
    )


def account_skill_provisioning_conflict(
    configured_database_parent: Path | str,
    native_homes: tuple[Path, ...],
    *,
    skill_names: tuple[str, ...] | None = None,
) -> str | None:
    """Read account ownership before any provider write, including setup previews."""
    source = managed_skills_home(configured_database_parent).resolve()
    if skill_names is None:
        skill_names = tuple(path.name for path in source.iterdir() if path.is_dir())
    names = set(skill_names) | RETIRED_PANELS_SKILL_NAMES | WORKER_ONLY_SKILL_NAMES
    for home in native_homes:
        target = home.expanduser().resolve() / SKILLS_DIR_NAME
        if target.is_symlink():
            owner = target.resolve(strict=False)
            if owner != source:
                return _account_skill_conflict(target, str(owner))
            continue
        if target.exists() and not target.is_dir():
            return _account_skill_conflict(target, "an unowned entry")
        for name in sorted(names):
            entry = target / name
            if entry.is_symlink():
                owner = entry.resolve(strict=False)
                if owner != source / name:
                    return _account_skill_conflict(entry, str(owner))
            elif entry.exists():
                return _account_skill_conflict(entry, "an unowned entry")
    return None


def provision_account_skills(
    configured_database_parent: Path | str,
    native_homes: tuple[Path, ...] | None = None,
) -> str | None:
    """Add missing native links, without replacing or deleting any account entry."""
    try:
        return _provision_account_skills(configured_database_parent, native_homes)
    except OSError as error:
        return _account_skill_conflict(
            Path(error.filename or str(configured_database_parent)),
            f"an unreadable or conflicting account entry ({error})",
        )


def _provision_account_skills(
    configured_database_parent: Path | str, native_homes: tuple[Path, ...] | None
) -> str | None:
    homes = tuple(
        sorted(
            {
                path.resolve()
                for path in (account_skill_homes() if native_homes is None else native_homes)
            }
        )
    )
    with skill_publication_lock(configured_database_parent) as source:
        if conflict := account_skill_provisioning_conflict(configured_database_parent, homes):
            return conflict
        with ExitStack() as stack:
            # Separate Panels instances share these provider locks, not their DB locks.
            for home in homes:
                home.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    home / ".panels-skills.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
                )
                stack.callback(os.close, descriptor)
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                    return _account_skill_conflict(home / ".panels-skills.lock", "an unowned lock")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            if conflict := account_skill_provisioning_conflict(configured_database_parent, homes):
                return conflict
            hidden = RETIRED_PANELS_SKILL_NAMES | WORKER_ONLY_SKILL_NAMES
            names = sorted(
                path.name
                for path in source.iterdir()
                if path.name not in hidden and path.is_dir() and (path / SKILL_FILE_NAME).is_file()
            )
            for home in homes:
                target = home / SKILLS_DIR_NAME
                if target.is_symlink():
                    # Convert only our exact legacy root link, so hidden specialists
                    # stay out of the native catalog as they did before this guard.
                    parent_fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        if not _unlink_owned_account_link(parent_fd, SKILLS_DIR_NAME, home, source):
                            return _account_skill_conflict(target, "an unowned legacy link")
                    finally:
                        os.close(parent_fd)
                target.mkdir(exist_ok=True)
                descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    for name in os.listdir(descriptor):
                        entry = target / name
                        if not entry.is_symlink():
                            continue
                        if name in hidden or not entry.exists():
                            link = Path(os.readlink(name, dir_fd=descriptor))
                            owner = (link if link.is_absolute() else target / link).resolve(
                                strict=False
                            )
                            if owner == source / name:
                                if not _unlink_owned_account_link(
                                    descriptor, name, target, source / name
                                ):
                                    return _account_skill_conflict(entry, "a changed account link")
                    for name in names:
                        try:
                            os.symlink(source / name, name, dir_fd=descriptor)
                        except FileExistsError:
                            # Exclusive creation never overwrites a concurrent entrant.
                            try:
                                link = Path(os.readlink(name, dir_fd=descriptor))
                            except OSError:
                                return _account_skill_conflict(target / name, "an unowned entry")
                            owner = (link if link.is_absolute() else target / link).resolve(
                                strict=False
                            )
                            if owner != source / name:
                                return _account_skill_conflict(target / name, str(owner))
                finally:
                    os.close(descriptor)
    return None


def _unlink_owned_account_link(directory: int, name: str, parent: Path, expected: Path) -> bool:
    """Recheck the exact owned link under the provider lock before cleanup."""
    before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISLNK(before.st_mode):
        return False
    link = Path(os.readlink(name, dir_fd=directory))
    owner = (link if link.is_absolute() else parent / link).resolve(strict=False)
    after = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if owner != expected or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        return False
    os.unlink(name, dir_fd=directory)
    return True


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
        if path.name not in hidden_skills and path.is_dir() and (path / SKILL_FILE_NAME).is_file()
    }
    if target.is_symlink():
        legacy_root = target.resolve(strict=False)
        target.unlink()
        target.mkdir(exist_ok=True)
        if legacy_root.is_dir():
            for legacy_skill in legacy_root.iterdir():
                if legacy_skill.name in panels_skill_names or legacy_skill.name in hidden_skills:
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
