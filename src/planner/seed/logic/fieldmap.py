"""Field-value mapping helpers: priority resolution (label then urgency
fallback), project parsing, and the ``PN:`` prefix splitter. Pure."""

from __future__ import annotations

import re

from planner.core.contracts import Priority, Project
from planner.seed.contracts import PRIORITY_MAP, PROJECT_MAP, URGENCY_MAP

_PN_RE = re.compile(r"^(P[0-3]): (.*)$")


def resolve_priority(priority_raw: str | None, urgency_raw: str | None) -> Priority:
    """A recognized Priority label wins; else a recognized (case-insensitive)
    Urgency value; else the P3 default."""
    if priority_raw is not None and priority_raw in PRIORITY_MAP:
        return PRIORITY_MAP[priority_raw]
    if urgency_raw is not None:
        key = urgency_raw.strip().lower()
        if key in URGENCY_MAP:
            return URGENCY_MAP[key]
    return Priority.P3


def parse_project(raw: str | None) -> Project | None:
    if raw is None:
        return None
    return PROJECT_MAP.get(raw.strip())


def split_pn_prefix(text: str) -> tuple[Priority | None, str]:
    match = _PN_RE.match(text)
    if match is None:
        return None, text
    return PRIORITY_MAP[match.group(1)], match.group(2)
