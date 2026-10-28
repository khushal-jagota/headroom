"""Home isolation ownership and explicit test configuration contracts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
    validate_destination,
)

HOME_KEYS = {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "PLAN_HERMES_HOME", "HERMES_HOME"}


def test_fresh_mapping_replaces_every_override_and_restores_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in HOME_KEYS:
        monkeypatch.setenv(name, str(tmp_path / "ambient" / name))
    original = {name: os.environ[name] for name in HOME_KEYS}
    first = create_synthetic_homes(tmp_path)
    second = create_synthetic_homes(tmp_path)
    assert set(first) == HOME_KEYS
    assert first["PLAN_HERMES_HOME"] == first["HERMES_HOME"]
    assert set(first.values()).isdisjoint(second.values())
    for destination in first.values():
        assert Path(destination).is_relative_to(tmp_path.resolve())
        assert Path(destination).is_dir()
    with synthetic_home_environment(first):
        assert {name: os.environ[name] for name in HOME_KEYS} == first
    assert {name: os.environ[name] for name in HOME_KEYS} == original


def test_existing_symlink_and_lexical_escapes_are_rejected_before_creation(
    tmp_path: Path,
) -> None:
    homes = create_synthetic_homes(tmp_path)
    root = Path(homes["HOME"]).parent
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    for destination in (root / "escape/new-profile", root / "../new-profile"):
        with pytest.raises(ValueError, match="outside|escapes"):
            validate_destination(root, destination).mkdir(parents=True)
    assert list(outside.iterdir()) == []
    assert not (tmp_path / "new-profile").exists()


def test_explicit_configuration_can_replace_and_remove_synthetic_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The autouse fixture must run first, then allow a case to exercise fallback rules.
    default_home = Path(os.environ["HOME"])
    assert default_home.is_relative_to(tmp_path)
    fallback = tmp_path / "explicit-home"
    fallback.mkdir()
    monkeypatch.setenv("HOME", str(fallback))
    for name in HOME_KEYS - {"HOME"}:
        monkeypatch.delenv(name, raising=False)
    assert Path.home() == fallback
    assert all(name not in os.environ for name in HOME_KEYS - {"HOME"})
