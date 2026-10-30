"""Disposable profile validation and mount helpers for the profile-only proof.

A profile stays at its canonical path and retains its actual inode identity. Only
an existing real skills directory can form the exact read-only submount needed
for root-level credential replacement and refresh locks.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path


class ProviderRefusal(ValueError):
    """This profile cannot receive a writable exposure safely."""


@dataclass(frozen=True)
class ProviderProfile:
    profile: Path
    skills: Path
    protected_targets: tuple[Path, ...]


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _entries(root: Path) -> Iterator[Path]:
    yield root
    if root.is_dir() and not root.is_symlink():

        def refuse_scan(error: OSError) -> None:
            raise ProviderRefusal("provider filesystem scan cannot establish protection") from error

        for parent, directories, files in os.walk(root, followlinks=False, onerror=refuse_scan):
            for name in [*directories, *files]:
                yield Path(parent) / name


def validate_profile(profile: Path, protected: Sequence[Path]) -> ProviderProfile:
    if not profile.is_absolute() or not profile.exists():
        raise ProviderRefusal("provider profile must be an existing canonical directory")
    if profile != profile.resolve(strict=True):
        raise ProviderRefusal("provider profile requires unambiguous canonical ancestry")
    if not profile.is_dir() or profile.is_symlink():
        raise ProviderRefusal("provider profile must be an existing real directory")
    skills = profile / "skills"
    if skills.is_symlink() or not skills.is_dir():
        raise ProviderRefusal("provider skills root must be an existing real directory")
    targets: set[Path] = set()
    mount_targets: set[Path] = set()
    identities: set[tuple[int, int]] = set()
    protected_entries = [*protected, skills]
    for lexical in protected_entries:
        target = lexical.resolve(strict=False)
        # The only permitted protected content within the writable profile is
        # below the exact skills submount. Ancestors remain destructive routes.
        for candidate in (lexical, target):
            if _overlap(candidate, profile) and not candidate.is_relative_to(skills):
                raise ProviderRefusal("protected target overlaps the writable provider profile")
        if target.exists():
            targets.add(target)
            for entry in _entries(target):
                item = entry.stat(follow_symlinks=False)
                identities.add((item.st_dev, item.st_ino))
        elif _overlap(target, profile):
            raise ProviderRefusal("absent protected target overlaps provider profile")
    # Skills can contain canonical links, but each external target needs its own
    # read-only mount. Broken links cannot prove exact target protection.
    pending = [skills]
    visited: set[Path] = set()
    while pending:
        root = pending.pop()
        if root in visited:
            continue
        visited.add(root)
        for entry in _entries(root):
            if entry.is_symlink():
                try:
                    target = entry.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ProviderRefusal("dangling or cyclic skill target") from exc
                if not target.is_relative_to(skills):
                    if _overlap(target, profile):
                        raise ProviderRefusal("skill target overlaps writable profile")
                    targets.add(target)
                    mount_targets.add(target)
                    pending.append(target)
            item = entry.stat(follow_symlinks=False)
            if not (
                stat.S_ISREG(item.st_mode)
                or stat.S_ISDIR(item.st_mode)
                or stat.S_ISLNK(item.st_mode)
            ):
                raise ProviderRefusal("protected skill target contains host IPC or device entry")
            identities.add((item.st_dev, item.st_ino))
    for entry in _entries(profile):
        if entry.is_relative_to(skills):
            continue
        item = entry.stat(follow_symlinks=False)
        if not (
            stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode) or stat.S_ISLNK(item.st_mode)
        ):
            raise ProviderRefusal("provider profile contains a host IPC or device entry")
        if (item.st_dev, item.st_ino) in identities:
            raise ProviderRefusal("writable provider entry aliases protected inode")
        if entry.is_symlink():
            target = entry.resolve(strict=False)
            if target.exists() and not (target.is_file() or target.is_dir()):
                raise ProviderRefusal("provider link exposes host IPC or device entry")
            if any(_overlap(target, protected_target) for protected_target in targets):
                raise ProviderRefusal("writable provider link aliases protected content")
        if stat.S_ISREG(item.st_mode) and item.st_nlink != 1:
            # Unknown hardlinks can provide an unenumerated writable alias.
            raise ProviderRefusal("provider profile contains a multiply linked writable file")
    return ProviderProfile(profile, skills, tuple(sorted(mount_targets)))


def profile_mount_args(profile: ProviderProfile) -> list[str]:
    result = ["--bind", str(profile.profile), str(profile.profile)]
    roots = sorted({profile.skills, *profile.protected_targets}, key=lambda value: len(value.parts))
    for root in roots:
        result.extend(["--ro-bind", str(root), str(root)])
    return result
