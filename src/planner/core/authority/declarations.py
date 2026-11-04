"""Declared authority for Worker types."""

from __future__ import annotations

from typing import Final

from planner.core.authority.contracts import Target

STANDS_ABOVE_BY_WORKER_TYPE: Final[dict[str, tuple[Target, ...]]] = {}


def stands_above_for_worker_type(worker_type: str) -> tuple[Target, ...]:
    """What a Ticket of this Worker type stands above. Empty for almost every type."""
    return STANDS_ABOVE_BY_WORKER_TYPE.get(worker_type, ())


__all__ = ["STANDS_ABOVE_BY_WORKER_TYPE", "stands_above_for_worker_type"]
