"""Acceptance item 21 — instrument integrity.

Two halves, both required by SPEC item 21:
  1. scan_test_files detects all six forbidden §18.2 patterns in a synthetic
     file set and reports none for a clean set.
  2. PLAN_FAKE_NOW is honored only when PLAN_TEST_MODE is set (via load_config +
     build_clock with explicit env dicts).

This test file is itself scanned when verify sweeps tests/, so the forbidden
literals must never appear verbatim in this source. Every one is assembled by
concatenation below (SKIP_MARKER, SKIP_CALL, XFAIL, ONLY, COMMENTED_DEF) so the
scanner sees only runtime-built strings, not matchable text.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import verify_lib  # noqa: E402

from planner.core.clock import RealClock, build_clock  # noqa: E402
from planner.core.clock import (
    TestClock as _TestClock,  # noqa: E402  aliased: pytest would try to collect a bare `TestClock`
)
from planner.core.config import load_config  # noqa: E402

# Forbidden §18.2 literals, assembled so this source never contains them verbatim.
SKIP_MARKER = "@pytest.mark." + "skip"
SKIP_CALL = "pytest." + "skip("
XFAIL = "xf" + "ail"
ONLY = "." + "only"
COMMENTED_DEF = "# def test_" + "disabled():"


def _write(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


def test_a21_instrument_integrity(tmp_path: Path) -> None:
    # --- half 1: the skip-scan detects all six forbidden patterns ---
    dirty = tmp_path / "dirty"
    dirty.mkdir()

    _write(
        dirty,
        "test_marker.py",
        f"import pytest\n\n\n{SKIP_MARKER}\ndef test_one():\n    assert True\n",
    )
    _write(
        dirty,
        "test_call.py",
        f"import pytest\n\n\ndef test_two():\n    {SKIP_CALL}'nope')\n    assert True\n",
    )
    _write(
        dirty,
        "test_expected_failure.py",
        f"import pytest\n\n\n@pytest.mark.{XFAIL}\ndef test_three():\n    assert True\n",
    )
    _write(
        dirty,
        "test_only.py",
        f"def test_four():\n    thing{ONLY}(True)\n    assert True\n",
    )
    _write(
        dirty,
        "test_empty.py",
        "def test_five():\n    pass\n",
    )
    _write(
        dirty,
        "test_commented.py",
        f"{COMMENTED_DEF}\n#     assert True\n\n\ndef test_six():\n    assert True\n",
    )

    violations = verify_lib.scan_test_files([dirty])
    expected_violations = {
        verify_lib.Violation(str(dirty / "test_marker.py"), SKIP_MARKER),
        verify_lib.Violation(str(dirty / "test_call.py"), SKIP_CALL),
        verify_lib.Violation(str(dirty / "test_expected_failure.py"), XFAIL),
        verify_lib.Violation(str(dirty / "test_only.py"), ONLY),
        verify_lib.Violation(str(dirty / "test_empty.py"), "empty test body"),
        verify_lib.Violation(str(dirty / "test_commented.py"), "commented-out test"),
    }
    # exact (file, pattern) mapping — each pattern attributed to its own file,
    # exactly one violation per synthetic file, no duplicates or misattribution.
    assert set(violations) == expected_violations
    assert len(violations) == len(expected_violations)

    # --- half 1b: a clean set yields no violations ---
    clean = tmp_path / "clean"
    clean.mkdir()
    _write(
        clean,
        "test_clean.py",
        "def test_real():\n    value = 1 + 1\n    assert value == 2\n",
    )
    _write(
        clean,
        "test_clean_two.py",
        'def test_also_real():\n    """A docstring is fine when the body does work."""\n'
        "    assert sum([1, 2, 3]) == 6\n",
    )
    assert verify_lib.scan_test_files([clean]) == []

    # --- half 2: PLAN_FAKE_NOW honored only under PLAN_TEST_MODE ---
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


def test_check_css_syntax_clean_and_each_failure_mode() -> None:
    clean = '/* header */\n.card { content: "}{"; }\n@media (a) { .x { color: red; } }\n'
    assert verify_lib.check_css_syntax(clean) == []
    assert verify_lib.check_css_syntax("") == []

    assert verify_lib.check_css_syntax(".a { color: red;") == ["1 unclosed '{'"]
    assert verify_lib.check_css_syntax(".a { }\n}\n") == ["line 2: unexpected '}'"]
    assert verify_lib.check_css_syntax("/* never closed\n.a { }\n") == [
        "line 1: unterminated block comment"
    ]
    assert verify_lib.check_css_syntax('.a::before { content: "oops\n; }') == [
        "line 1: unterminated string"
    ]
    assert verify_lib.check_css_syntax('.a::before { content: "runs off the end') == [
        "line 1: unterminated string",
        "1 unclosed '{'",
    ]
    # braces inside strings and comments never count; an escaped newline is a
    # legal string continuation (LF or CRLF), not a termination error.
    assert verify_lib.check_css_syntax('/* { */ .a { content: "\\\n}"; }') == []
    assert verify_lib.check_css_syntax('.a { content: "x\\\r\ny"; }') == []
