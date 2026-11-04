"""Home isolation ownership and explicit test configuration contracts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    shared_package_caches,
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
    # Protocol routing precedes fixtures, then permits explicit fallback configuration.
    default_home = Path(os.environ["HOME"])
    assert default_home.is_dir()
    assert all(Path(os.environ[name]).is_relative_to(default_home)
               for name in HOME_KEYS - {"HOME"})
    assert not default_home.is_relative_to(tmp_path)
    for name in ("PIP_CACHE_DIR", "npm_config_cache"):
        assert Path(os.environ[name]).is_absolute()
        assert not Path(os.environ[name]).is_relative_to(default_home)
    fallback = tmp_path / "explicit-home"
    fallback.mkdir()
    monkeypatch.setenv("HOME", str(fallback))
    for name in HOME_KEYS - {"HOME"}:
        monkeypatch.delenv(name, raising=False)
    assert Path.home() == fallback
    assert all(name not in os.environ for name in HOME_KEYS - {"HOME"})


def test_package_caches_survive_distinct_private_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_home = tmp_path / "launch"
    caches = shared_package_caches({"HOME": str(launch_home)})
    assert caches == {
        "PIP_CACHE_DIR": str(launch_home / ".cache/pip"),
        "npm_config_cache": str(launch_home / ".npm"),
    }
    assert shared_package_caches({
        "HOME": str(launch_home), "XDG_CACHE_HOME": str(tmp_path / "xdg"),
    })["PIP_CACHE_DIR"] == str(tmp_path / "xdg/pip")
    explicit = {name: str(tmp_path / name) for name in caches}
    assert shared_package_caches({"HOME": str(launch_home), **explicit}) == explicit
    for name, value in explicit.items():
        monkeypatch.setenv(name, value)
    for _ in range(2):
        homes = create_synthetic_homes(tmp_path)
        with synthetic_home_environment({**caches, **homes}):
            assert {name: os.environ[name] for name in caches} == caches
            assert Path.home() == Path(homes["HOME"])
            assert all(not Path(value).is_relative_to(Path.home()) for value in caches.values())
    assert {name: os.environ[name] for name in caches} == explicit
