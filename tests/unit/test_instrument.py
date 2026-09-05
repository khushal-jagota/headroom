"""Focused proof for the verify mode, CSS gate, and real/test clock boundary."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import verify_lib  # type: ignore[import-not-found]  # noqa: E402  # reached via sys.path.insert above; not visible to mypy

from planner.core.clock import RealClock, build_clock  # noqa: E402
from planner.core.clock import (
    TestClock as _TestClock,  # noqa: E402  aliased: pytest would try to collect a bare `TestClock`
)
from planner.core.config import load_config  # noqa: E402


def test_verify_modes_and_test_clock_isolation(tmp_path: Path) -> None:
    assert verify_lib.parse_verify_mode([]) == "full"
    assert verify_lib.parse_verify_mode(["full"]) == "full"
    assert verify_lib.parse_verify_mode(["fast"]) == "fast"
    assert verify_lib.parse_verify_mode(["integration"]) == "integration"
    assert verify_lib.parse_verify_mode(["e2e"]) == "e2e"
    for invalid in (["quick"], ["fast", "e2e"]):
        try:
            verify_lib.parse_verify_mode(invalid)
        except ValueError as exc:
            assert str(exc) == "usage: ./verify [full|fast|integration|e2e]"
        else:
            raise AssertionError(f"accepted invalid verify mode: {invalid}")

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
