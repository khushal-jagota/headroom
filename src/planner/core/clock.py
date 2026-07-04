"""The clock. Real time in production; a mutable fake in test mode so planning-date
math (§6.1) and the set-now test endpoint (D5) are deterministic. The choice is
made once at startup from config."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from planner.core.config import Config


class Clock(Protocol):
    def now(self) -> datetime: ...       # timezone-aware, LOCAL time (§6.1 math is local)
    def now_unix(self) -> int: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now().astimezone()

    def now_unix(self) -> int:
        return int(self.now().timestamp())


class TestClock:
    """Mutable clock for PLAN_FAKE_NOW and POST /api/test/set-now (D5)."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def now_unix(self) -> int:
        return int(self._now.timestamp())

    def set(self, now: datetime) -> None:
        self._now = now


def parse_fake_now(raw: str) -> datetime:
    """PLAN_FAKE_NOW is ISO; a naive value is interpreted as local time."""
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def build_clock(config: Config) -> Clock:
    if config.test_mode and config.fake_now:
        return TestClock(parse_fake_now(config.fake_now))
    return RealClock()
