"""Framework-free contracts for bounded list reads."""

from __future__ import annotations

from dataclasses import dataclass

from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT


@dataclass(frozen=True, slots=True)
class ListPageRequest:
    limit: int = DEFAULT_LIST_LIMIT
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise PlannerError(
                ErrorCode.validation,
                "limit must be at least 1",
                {"limit": self.limit},
            )
        if self.offset < 0:
            raise PlannerError(
                ErrorCode.validation,
                "offset must be at least 0",
                {"offset": self.offset},
            )


@dataclass(frozen=True, slots=True)
class ListPage[Row]:
    rows: tuple[Row, ...]
    match_count: int
    limit: int
    offset: int

    @property
    def return_count(self) -> int:
        return len(self.rows)

    @property
    def omitted_before(self) -> int:
        return min(self.offset, self.match_count)

    @property
    def omitted_after(self) -> int:
        return max(self.match_count - self.omitted_before - self.return_count, 0)

    @property
    def complete(self) -> bool:
        return self.omitted_before == 0 and self.omitted_after == 0

    @property
    def next_offset(self) -> int | None:
        if self.omitted_after == 0:
            return None
        return self.omitted_before + self.return_count

    def facts_json(self) -> JsonDict:
        return {
            "match_count": self.match_count,
            "return_count": self.return_count,
            "limit": self.limit,
            "offset": self.offset,
            "omitted_before": self.omitted_before,
            "omitted_after": self.omitted_after,
            "complete": self.complete,
            "next_offset": self.next_offset,
        }

    def response(self, key: str) -> JsonDict:
        return {key: list(self.rows), "page": self.facts_json()}
