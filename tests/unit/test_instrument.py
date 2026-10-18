"""Focused proof for the environment check and the real/test clock boundary."""

from __future__ import annotations

import builtins
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

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


def test_a_launcher_keeps_the_virtualenv_it_names(tmp_path: Path) -> None:
    """Two ways to throw the signal away, and a healthy tree must survive both.

    ``.venv/bin/python`` is a symlink chain ending at the system interpreter, so
    resolving the whole path makes every launcher look foreign. Resolving none of it
    makes a tree reached through a symlink look foreign instead. Only the directory
    is resolved, and both healthy trees pass.
    """
    real = tmp_path.resolve() / "real"
    bin_dir = real / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    system_python = tmp_path.resolve() / "usr" / "bin" / "python3.13"
    system_python.parent.mkdir(parents=True)
    system_python.write_text("", encoding="utf-8")
    (bin_dir / "python").symlink_to(system_python)
    launcher = bin_dir / "pytest"
    launcher.write_text(f"#!{bin_dir / 'python'}\nrest of the script\n", encoding="utf-8")

    def only_launcher(path: Path) -> tree_environment.EnvironmentAnswers:
        return tree_environment.EnvironmentAnswers(
            launcher_interpreters={"pytest": tree_environment.read_launcher_interpreter(path)}
        )

    assert tree_environment.read_launcher_interpreter(launcher) == bin_dir / "python"
    assert tree_environment.faults(real, only_launcher(launcher)) == []

    # The same tree reached through a symlink is the same tree.
    link = tmp_path.resolve() / "link"
    link.symlink_to(real)
    assert tree_environment.faults(link, only_launcher(link / ".venv" / "bin" / "pytest")) == []

    # A native binary and a missing file both read as "no answer", not as a fault.
    (bin_dir / "ruff").write_bytes(b"\x7fELF\x02\x01\x01\x00" + bytes(64))
    assert tree_environment.read_launcher_interpreter(bin_dir / "ruff") is None
    assert tree_environment.read_launcher_interpreter(bin_dir / "absent") is None


def test_launcher_and_pyvenv_cfg_readers_answer_only_when_the_file_says_so() -> None:
    venv_python = "/home/vps/tree/.venv/bin/python"
    assert tree_environment.interpreter_from_launcher(f"#!{venv_python}") == Path(venv_python)
    assert tree_environment.interpreter_from_launcher(f"#!/usr/bin/env {venv_python}") == Path(
        venv_python
    )

    # pip writes a two-line /bin/sh wrapper instead of a shebang once the interpreter
    # path passes 127 bytes, which a long worktree name reaches on its own. Read the
    # first line alone and every launcher in that tree reports /bin/sh.
    wrapper = "#!/bin/sh\n" + "'''exec' " + venv_python + ' "$0" "$@"\n' + "' '''\n"
    assert tree_environment.interpreter_from_launcher(wrapper) == Path(venv_python)

    # A native binary, a relative interpreter, and an empty line carry no answer.
    assert tree_environment.interpreter_from_launcher("\x7fELF\x02\x01\x01") is None
    assert tree_environment.interpreter_from_launcher("#!python3") is None
    assert tree_environment.interpreter_from_launcher("#!") is None
    assert tree_environment.interpreter_from_launcher("") is None

    created_at = "/home/vps/tree/.venv"
    cfg = "home = /usr/bin\nversion = 3.13.5\n"
    cfg += f"command = /usr/bin/python3 -m venv {created_at}\n"
    assert tree_environment.venv_origin_from_pyvenv_cfg(cfg) == Path(created_at)
    # CPython writes a spaced target without quoting it in the command record.
    assert tree_environment.venv_origin_from_pyvenv_cfg(
        f"command = /usr/bin/python3 -m venv --without-pip {created_at} with spaces\n"
    ) == Path(f"{created_at} with spaces")
    assert tree_environment.venv_origin_from_pyvenv_cfg(
        f"command = /usr/bin/python3 -m venv {created_at} with /segment/.venv\n"
    ) == Path(f"{created_at} with /segment/.venv")
    # uv and virtualenv write no command line, so the check stays silent rather than guesses.
    assert tree_environment.venv_origin_from_pyvenv_cfg("home = /usr/bin\nuv = 0.12.5\n") is None


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


@pytest.mark.parametrize("line", ["-r other.txt", "thing>=1", "thing==1.*", "thing @ file:///x"])
def test_unsupported_requirements_refuse_with_the_source_line(
    tmp_path: Path, line: str
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"# heading\n{line}\n", encoding="utf-8")
    found = tree_environment.dependency_faults(tmp_path)
    assert len(found) == 1
    assert f"{requirements}:2:" in found[0]
    assert line in found[0]


@pytest.mark.parametrize("installed", [None, "25.0"])
def test_missing_or_unusable_parser_reports_bootstrap_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: str | None
) -> None:
    original_import = builtins.__import__

    def broken_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if name == "packaging.requirements":
            raise ImportError("parser unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    monkeypatch.setattr(tree_environment, "installed_version", lambda name: installed)
    (tmp_path / "requirements.txt").write_text("  packaging==26.2\n", encoding="utf-8")
    found = tree_environment.dependency_faults(tmp_path)
    assert len(found) == 1
    assert "bootstrap failure" in found[0]
    assert "required packaging==26.2" in found[0]
    assert f"installed {installed or 'absent'}" in found[0]


def test_preflight_checks_identity_then_dependencies_before_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asks: list[bool] = []

    def answers(tree: Path, *, ask_planner: bool) -> tree_environment.EnvironmentAnswers:
        asks.append(ask_planner)
        if ask_planner:
            raise ModuleNotFoundError("unrelated import failure", name="transitive")
        return tree_environment.EnvironmentAnswers(venv_prefix=tree / ".venv")

    monkeypatch.setattr(tree_environment, "running_answers", answers)
    monkeypatch.setattr(tree_environment, "dependency_faults", lambda tree: ["stale SDK"])
    diagnosis = tree_environment.preflight(tmp_path, ask_planner=True)
    assert diagnosis is not None and "dependency drift" in diagnosis
    assert "stale SDK" in diagnosis
    assert asks == [False]
    monkeypatch.setattr(tree_environment, "dependency_faults", lambda tree: [])
    with pytest.raises(ModuleNotFoundError, match="unrelated import failure"):
        tree_environment.preflight(tmp_path, ask_planner=True)
    assert asks == [False, False, True]

    monkeypatch.setattr(
        tree_environment,
        "running_answers",
        lambda tree, **kwargs: tree_environment.EnvironmentAnswers(venv_prefix=tree / "foreign"),
    )
    monkeypatch.setattr(
        tree_environment, "dependency_faults", lambda tree: pytest.fail("identity must run first")
    )
    diagnosis = tree_environment.preflight(tmp_path, ask_planner=True)
    assert diagnosis is not None and "Rebuild the virtualenv" in diagnosis
    assert "dependency drift" not in diagnosis


def test_editable_identity_ignores_other_editable_distributions(tmp_path: Path) -> None:
    site = tmp_path / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    (site / "__editable__.another-1.pth").write_text("/other/src\n", encoding="utf-8")
    (site / "__editable__.planner-2.pth").write_text("/this/src\n", encoding="utf-8")
    assert tree_environment.read_editable_root(tmp_path) == Path("/this/src")


@pytest.mark.parametrize("missing_name", [None, "planner.internal", "transitive", "planner"])
def test_planner_import_only_reports_actual_top_level_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_name: str | None
) -> None:
    original_import = builtins.__import__
    failure = ImportError("source import failed", name=missing_name)

    def broken_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if name == "planner":
            raise failure
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    if missing_name == "planner":
        assert tree_environment.running_answers(tmp_path, ask_planner=True).planner_absent
    else:
        with pytest.raises(ImportError) as caught:
            tree_environment.running_answers(tmp_path, ask_planner=True)
        assert caught.value is failure
