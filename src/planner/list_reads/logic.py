"""Pure page selection for stable, already-filtered rows."""

from __future__ import annotations

from collections.abc import Sequence

from planner.list_reads.contracts import ListPage, ListPageRequest


def select_page[Row](rows: Sequence[Row], request: ListPageRequest) -> ListPage[Row]:
    return ListPage(
        rows=tuple(rows[request.offset : request.offset + request.limit]),
        match_count=len(rows),
        limit=request.limit,
        offset=request.offset,
    )
