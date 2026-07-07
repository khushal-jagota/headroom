"""runtime — the code-side systems that drive work (W3).

System B (set-off / run primitive, the sole writer of ticket run-status) lands in
W3a; System A (readiness poll + fast path) plus the shared readiness predicate and the
singleton machine-lock consumer land in W3b. Kept as a package so both systems share one
home.
"""

from __future__ import annotations

from planner.runtime.readiness import is_runnable
from planner.runtime.system_a import SystemA
from planner.runtime.system_b import SystemB

__all__ = ["SystemA", "SystemB", "is_runnable"]
