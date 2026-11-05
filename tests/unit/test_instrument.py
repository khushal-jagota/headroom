"""Focused proof for the environment check and the real/test clock boundary."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import tree_environment  # noqa: E402  # reached via sys.path.insert above

from planner.core.clock import RealClock, build_clock  # noqa: E402
from planner.core.clock import (
    TestClock as _TestClock,  # noqa: E402  aliased: pytest would try to collect a bare `TestClock`
)
from planner.core.config import load_config  # noqa: E402


def test_test_clock_is_used_only_in_test_mode(tmp_path: Path) -> None:
    fake_iso = "2021-01-02T03:04:05"
    # the clock reads a naive ISO fake exactly as parse_fake_now does: interpret it
    # as local wall time, kept timezone-aware. Compute the same value to compare against.
    fake_aware = datetime.fromisoformat(fake_iso).astimezone()

    # both set: load_config keeps fake_now, build_clock returns a TestClock at it
    on_env = {"PLAN_TEST_MODE": "1", "PLAN_FAKE_NOW": fake_iso}
    on_cfg = load_config(path=str(tmp_path / "absent.yaml"), env=on_env)
    assert on_cfg.test_mode is True
    assert on_cfg.fake_now == fake_iso
    on_clock = build_clock(on_cfg)
    assert isinstance(on_clock, _TestClock)
    assert on_clock.now() == fake_aware  # exact instant, timezone-aware
    assert on_clock.now().tzinfo is not None
    assert on_clock.now_unix() == int(fake_aware.timestamp())

    # only PLAN_FAKE_NOW: config discards it and build_clock falls back to real
    off_env = {"PLAN_FAKE_NOW": fake_iso}
    off_cfg = load_config(path=str(tmp_path / "absent.yaml"), env=off_env)
    assert off_cfg.test_mode is False
    assert off_cfg.fake_now is None
    off_clock = build_clock(off_cfg)
    assert isinstance(off_clock, RealClock)
    # a RealClock reads live wall time, never the discarded fake: its reading lands
    # between two real-time samples taken around the call, and is not the fake instant.
    before = datetime.now().astimezone()
    observed = off_clock.now()
    after = datetime.now().astimezone()
    assert before <= observed <= after
    assert observed != fake_aware


def test_environment_checks_the_resolved_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importlib.machinery import ModuleSpec

    tree = tmp_path / "mine"
    own_source = tree / "src" / "planner" / "__init__.py"
    foreign_source = tmp_path / "live" / "src" / "planner" / "__init__.py"
    linked_source = tree / "src" / "linked-planner"
    linked_source.parent.mkdir(parents=True)
    linked_source.symlink_to(foreign_source.parent, target_is_directory=True)
    cases = (
        (own_source, True),
        (foreign_source, False),
        (linked_source / "__init__.py", False),
        (None, False),
    )
    for origin, accepted in cases:
        spec = ModuleSpec("planner", loader=None, origin=str(origin) if origin else None)
        monkeypatch.setattr(
            importlib.util, "find_spec", lambda name, found=spec: found
        )
        diagnosis = tree_environment.preflight(tree)
        assert (diagnosis is None) is accepted
        if diagnosis:
            assert "outside" in diagnosis
            assert str(tree / "src") in diagnosis
