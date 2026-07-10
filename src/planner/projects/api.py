"""Project catalog routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from planner.core.authctx import require_direct_write
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.projects import data as projects_data
from planner.projects.contracts import CreateProjectBody, UpdateProjectBody
from planner.tickets.api import Clk, Ctx, DbConn, body_str

router = APIRouter()


def _marshal_create_project(raw: JsonDict) -> CreateProjectBody:
    return CreateProjectBody(name=body_str(raw, "name"), summary=body_str(raw, "summary"))


def _marshal_update_project(raw: JsonDict) -> UpdateProjectBody:
    body = UpdateProjectBody()
    if "name" in raw:
        body["name"] = body_str(raw, "name")
    if "summary" in raw:
        body["summary"] = body_str(raw, "summary")
    if not body:
        raise PlannerError(ErrorCode.validation, "no project fields to update", {})
    return body


@router.get("/projects")
async def list_projects(conn: DbConn) -> JsonDict:
    return {"projects": [projects_data.project_json(p) for p in projects_data.list_projects(conn)]}


@router.post("/projects")
async def create_project(raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    require_direct_write(ctx)
    body = _marshal_create_project(raw)
    project = projects_data.create_project(
        conn, name=body["name"], summary=body["summary"], now=clk.now_unix()
    )
    return projects_data.project_json(project)


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    require_direct_write(ctx)
    body = _marshal_update_project(raw)
    project = projects_data.update_project(
        conn,
        project_id,
        name=body.get("name"),
        summary=body.get("summary"),
        now=clk.now_unix(),
    )
    return projects_data.project_json(project)
