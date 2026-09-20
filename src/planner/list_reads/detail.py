"""The detail level of an object read.

One address answers for an object, and ``detail`` says how much of it comes back.
``detail`` is required and has no default. The collection address of each object means
the full level today, so a default would quietly change what that address returns for a
caller that has not moved. Required means such a caller fails loudly instead.

A parameter that belongs to the other level is refused rather than ignored. Silently
dropping ``search`` on a full read would leave one address behaving as two.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from planner.core.errors import ErrorCode, PlannerError


class ReadDetail(StrEnum):
    summary = "summary"
    full = "full"


def parse_read_detail(raw: str | None) -> ReadDetail:
    """Resolve the ``detail`` query parameter, which callers must supply."""
    allowed = [level.value for level in ReadDetail]
    if raw is None:
        raise PlannerError(
            ErrorCode.validation,
            "detail is required",
            {"allowed": allowed},
        )
    try:
        return ReadDetail(raw)
    except ValueError:
        raise PlannerError(
            ErrorCode.validation,
            "unknown detail",
            {"detail": raw, "allowed": allowed},
        ) from None


def reject_parameters(because: str, given: Mapping[str, object]) -> None:
    """Refuse parameters that do not apply to the read the caller asked for."""
    named = sorted(name for name, value in given.items() if value is not None)
    if named:
        raise PlannerError(
            ErrorCode.validation,
            "parameter does not apply to this read",
            {"parameters": named, "because": because},
        )


def require_full_for_one(level: ReadDetail) -> None:
    """Reading one object by id answers at the full level only.

    No object has a single-row summary shape today, and this Ticket adds none.
    """
    if level is not ReadDetail.full:
        raise PlannerError(
            ErrorCode.validation,
            "reading one object requires detail=full",
            {"detail": level.value},
        )
