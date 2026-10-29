"""Goal fact, page, and measure projection HTTP boundary."""

from typing import Any

from fastapi import APIRouter

from planner.goals import actions, data, views
from planner.tickets.api import Clk, Ctx, DbConn

router = APIRouter()


@router.get("/goals")
async def list_goals(
    conn: DbConn, clk: Clk, active: bool | None = None, project_id: str | None = None
) -> Any:
    return views.list_json(conn, views.calendar_now(clk), active=active, project_id=project_id)


@router.post("/goals")
async def create_goal(body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk) -> Any:
    return views.goal_json(
        conn,
        actions.create_goal(conn, body, principal=ctx.principal, now=clk.now_unix()),
        views.calendar_now(clk),
    )


@router.get("/goals/{goal_id}")
async def read_goal(goal_id: str, conn: DbConn, clk: Clk) -> Any:
    return views.goal_json(conn, data.read_goal(conn, goal_id), views.calendar_now(clk))


@router.patch("/goals/{goal_id}")
async def update_goal(goal_id: str, body: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk) -> Any:
    return views.goal_json(
        conn,
        actions.update_goal(conn, goal_id, body, principal=ctx.principal, now=clk.now_unix()),
        views.calendar_now(clk),
    )


@router.get("/goals/{goal_id}/readings")
async def readings(
    goal_id: str,
    conn: DbConn,
    clk: Clk,
    first_date: str | None = None,
    last_date: str | None = None,
) -> Any:
    return views.readings_json(
        conn, goal_id, views.calendar_now(clk), first_date=first_date, last_date=last_date
    )
