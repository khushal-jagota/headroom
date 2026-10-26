"""Focused proof for the environment check and the real/test clock boundary."""

from __future__ import annotations

import sys
from dataclasses import replace
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


def test_environment_faults_name_every_way_a_copied_virtualenv_redirects_a_gate(
    tmp_path: Path,
) -> None:
    tree = tmp_path.resolve() / "mine"
    (tree / "src").mkdir(parents=True)
    other = tmp_path.resolve() / "theirs"

    clean = tree_environment.EnvironmentAnswers(
        venv_prefix=tree / ".venv",
        venv_origin=tree / ".venv",
        editable_root=tree / "src",
        planner_file=tree / "src" / "planner" / "__init__.py",
        launcher_interpreters={"pytest": tree / ".venv" / "bin" / "python"},
    )
    assert tree_environment.faults(tree, clean) == []

    # Nothing collected is not a fault: a caller that cannot see an answer stays quiet.
    assert tree_environment.faults(tree, tree_environment.EnvironmentAnswers()) == []

    def only_fault(answers: tree_environment.EnvironmentAnswers) -> str:
        found = tree_environment.faults(tree, answers)
        assert len(found) == 1, found
        return str(found[0])

    assert "the running interpreter belongs to" in only_fault(
        replace(clean, venv_prefix=other / ".venv")
    )
    assert "was copied or moved" in only_fault(replace(clean, venv_origin=other / ".venv"))
    assert "the editable install in" in only_fault(replace(clean, editable_root=other / "src"))
    assert "planner is not installed" in only_fault(
        replace(clean, planner_absent=True, planner_file=None)
    )
    assert "outside" in only_fault(
        replace(clean, planner_file=other / "src" / "planner" / "__init__.py")
    )
    assert "pytest runs on" in only_fault(
        replace(clean, launcher_interpreters={"pytest": other / ".venv" / "bin" / "python"})
    )

    # A package installed into this tree's own virtualenv is not foreign, and saying it
    # is would send a reader hunting for a copied virtualenv that does not exist.
    installed = tree / ".venv" / "lib" / "python3.13" / "site-packages" / "planner"
    message = only_fault(
        replace(clean, editable_root=None, planner_file=installed / "__init__.py")
    )
    assert "rather than linked to" in message
    assert "pip install --editable ." in message

    # A native binary carries no interpreter, so it contributes no answer.
    assert tree_environment.faults(tree, replace(clean, launcher_interpreters={"ruff": None})) == []

    # Every fault is reported together, not one at a time.
    both = replace(clean, venv_prefix=other / ".venv", planner_absent=True)
    assert len(tree_environment.faults(tree, both)) == 2






def test_dependency_pins_use_distribution_metadata_and_pep508_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = {
        "claude-agent-sdk": "0.2.128", "missing": None,
        "equivalent": "1.0.0", "packaging": "26.1",
    }
    monkeypatch.setattr(tree_environment, "installed_version", installed.get)
    (tmp_path / "requirements.txt").write_text(
        "# This file can include comments and blank lines.\n\n"
        "Claude_Agent.SDK[extra]==0.2.152 # pinned SDK\n"
        "missing==1\nequivalent==1.0\npackaging==26.2\n"
        "inactive==1; python_version < '1'\n",
        encoding="utf-8",
    )
    assert tree_environment.dependency_faults(tmp_path) == [
        "claude-agent-sdk: required ==0.2.152; installed 0.2.128",
        "missing: required ==1; installed absent",
        "packaging: required ==26.2; installed 26.1",
    ]
    installed.update({"claude-agent-sdk": "0.2.152", "missing": "1.0", "packaging": "26.2"})
    # Unknown installed distributions have no bearing on the required pins.
    installed["unrelated-editable"] = "5"
    assert tree_environment.dependency_faults(tmp_path) == []
