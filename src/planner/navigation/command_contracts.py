"""The explicit HTTP command boundary for additive navigation fences."""

from __future__ import annotations

from dataclasses import dataclass

FENCE_HEADER = b"panels-navigation-fence"
COVERAGE_HEADER = b"panels-navigation-coverage"


@dataclass(frozen=True)
class CommandRoute:
    method: str
    path: str
    reason: str
