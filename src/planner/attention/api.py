"""Historical attention over selected dates, groups, and recorded work."""

from datetime import date

from fastapi import APIRouter

from planner.attention import actions
from planner.attention.configuration import DEFAULT_LIMIT
from planner.attention.contracts import GroupBy, Scope, ScopeKind, SummaryRequest
from planner.core.contracts import JsonDict
from planner.tickets.api import Clk, DbConn

router = APIRouter()


@router.get("/attention/summary")
def summary(
    conn: DbConn,
    clk: Clk,
    first_date: date | None = None,
    last_date: date | None = None,
    group_by: GroupBy = GroupBy.project,
    scope_kind: ScopeKind = ScopeKind.all,
    scope_id: str | None = None,
    sort: str = "owner_messages",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> JsonDict:
    """Where recorded owner involvement went, with preceding-period comparison.

    Dates are inclusive Europe/Berlin dates. Scope filters work independently of
    grouping. Missing history stays explicit. Gate waits are delays, not owner hours.
    This read does not report current demands or forecast future capacity.
    """
    return actions.read_summary(
        conn,
        SummaryRequest(
            first_date=first_date,
            last_date=last_date,
            group_by=group_by,
            scope=Scope(scope_kind, scope_id),
            sort=sort,
            limit=limit,
            offset=offset,
        ),
        now=clk.now_unix(),
    )
