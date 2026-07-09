"""Project catalog routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from planner.core.authctx import reject_agents
from planner.core.contracts import JsonDict
from planner.projects import data as projects_data
from planner.projects.contracts import CreateProjectBody
from planner.tickets.api import Clk, Ctx, DbConn, body_str

router = APIRouter()


def _marshal_create_project(raw: JsonDict) -> CreateProjectBody:
    return CreateProjectBody(name=body_str(raw, "name"))


@router.get("/projects")
async def list_projects(conn: DbConn) -> JsonDict:
    return {"projects": [projects_data.project_json(p) for p in projects_data.list_projects(conn)]}


@router.post("/projects")
async def create_project(raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk) -> JsonDict:
    reject_agents(ctx)
    body = _marshal_create_project(raw)
    project = projects_data.create_project(conn, name=body["name"], now=clk.now_unix())
    return projects_data.project_json(project)
