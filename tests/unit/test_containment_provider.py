"""Disposable profile alias validation for the profile-only proof."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_containment.provider import (
    ProviderRefusal,
    profile_mount_args,
    validate_profile,
)


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "canonical" / ".claude"
    (profile / "skills").mkdir(parents=True)
    (profile / "skills" / "sentinel").write_text("protected")
    (profile / "credentials.json").write_text("synthetic-auth")
    return profile


def test_provider_mount_preserves_canonical_identity(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    before = profile.stat()
    validated = validate_profile(profile, [profile / "skills"])
    args = profile_mount_args(validated)
    assert args[:3] == ["--bind", str(profile), str(profile)]
    assert args[3:] == ["--ro-bind", str(profile / "skills"), str(profile / "skills")]
    assert validated.profile.stat().st_ino == before.st_ino
    assert (profile / "credentials.json").read_text() == "synthetic-auth"


@pytest.mark.parametrize("layout", ["absent", "symlink", "dangling", "profile-alias"])
def test_provider_rejects_unsafe_skill_roots(tmp_path: Path, layout: str) -> None:
    profile = _profile(tmp_path)
    skills = profile / "skills"
    (skills / "sentinel").unlink()
    skills.rmdir()
    if layout in {"symlink", "dangling"}:
        target = tmp_path / "target"
        if layout == "symlink":
            target.mkdir()
        skills.symlink_to(target)
    if layout == "profile-alias":
        skills.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(profile, target_is_directory=True)
        profile = alias
    with pytest.raises(ProviderRefusal):
        validate_profile(profile, [])


@pytest.mark.parametrize("alias", ["hardlink", "symlink", "target-overlap", "ancestor"])
def test_provider_refuses_protected_writable_alias(tmp_path: Path, alias: str) -> None:
    profile = _profile(tmp_path)
    sentinel = profile / "skills" / "sentinel"
    protected = [profile / "skills"]
    if alias == "hardlink":
        os.link(sentinel, profile / "alias")
    elif alias == "symlink":
        (profile / "alias").symlink_to(sentinel)
    elif alias == "target-overlap":
        (profile / "skills" / "alias").symlink_to(profile / "credentials.json")
    else:
        protected.append(profile.parent)
    with pytest.raises(ProviderRefusal):
        validate_profile(profile, protected)


def test_provider_projects_external_target_without_other_accounts(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    target = tmp_path / "managed"
    target.mkdir()
    (target / "SKILL.md").write_text("sentinel")
    (profile / "skills" / "native").symlink_to(target, target_is_directory=True)
    other = tmp_path / "other-account" / "skills"
    other.mkdir(parents=True)
    validated = validate_profile(profile, [profile / "skills", other])
    assert validated.protected_targets == (target,)
    assert str(other) not in profile_mount_args(validated)
