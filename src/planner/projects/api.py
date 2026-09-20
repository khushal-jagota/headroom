"""Project catalog routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from planner.core.authctx import require_direct_write
from planner.core.contracts import JsonDict, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.list_reads.detail import (
    ReadDetail,
    parse_read_detail,
    reject_parameters,
    require_full_for_one,
)
from planner.projects import data as projects_data
from planner.projects.contracts import CreateProjectBody, UpdateProjectBody
from planner.tickets.api import Clk, Ctx, DbConn, body_str, parse_enum

router = APIRouter()


def _marshal_create_project(raw: JsonDict) -> CreateProjectBody:
    body = CreateProjectBody(
        name=body_str(raw, "name"),
        summary=body_str(raw, "summary"),
        priority=parse_enum(Priority, body_str(raw, "priority"), "priority"),
    )
    if "folder_path" in raw:
        body["folder_path"] = _nullable_folder_path(raw["folder_path"])
    return body


def _marshal_update_project(raw: JsonDict) -> UpdateProjectBody:
    body = UpdateProjectBody()
    if "name" in raw:
        body["name"] = body_str(raw, "name")
    if "summary" in raw:
        body["summary"] = body_str(raw, "summary")
    if "priority" in raw:
        body["priority"] = parse_enum(Priority, body_str(raw, "priority"), "priority")
    if "folder_path" in raw:
        body["folder_path"] = _nullable_folder_path(raw["folder_path"])
    if not body:
        raise PlannerError(ErrorCode.validation, "no project fields to update", {})
    return body


def _nullable_folder_path(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PlannerError(ErrorCode.validation, "invalid folder_path", {"folder_path": raw})
    return raw


@router.get("/projects")
async def read_projects(
    conn: DbConn,
    detail: str | None = None,
    object_id: Annotated[str | None, Query(alias="id")] = None,
    limit: int | None = None,
    offset: int | None = None,
) -> JsonDict:
    """Read Projects at the level the caller asks for, or one Project by id."""
    level = parse_read_detail(detail)
    if object_id is not None:
        require_full_for_one(level)
        reject_parameters("id", {"limit": limit, "offset": offset})
        return projects_data.project_json(projects_data.read_project(conn, object_id))
    if level is ReadDetail.full:
        reject_parameters("detail=full", {"limit": limit, "offset": offset})
        return {
            "projects": [
                projects_data.project_json(p) for p in projects_data.list_projects(conn)
            ]
        }
    page = projects_data.list_project_summaries(
        conn,
        page_request=ListPageRequest(
            limit=DEFAULT_LIST_LIMIT if limit is None else limit,
            offset=0 if offset is None else offset,
        ),
    )
    return page.response("projects")


@router.post("/projects")
async def create_project(
    raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    require_direct_write(ctx)
    body = _marshal_create_project(raw)
    project = projects_data.create_project(
        conn,
        name=body["name"],
        summary=body["summary"],
        priority=body["priority"],
        folder_path=body.get("folder_path"),
        now=clk.now_unix(),
    )
    return projects_data.project_json(project)


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, clk: Clk
) -> JsonDict:
    require_direct_write(ctx)
    body = _marshal_update_project(raw)
    folder_path: dict[str, Any] = {}
    if "folder_path" in body:
        folder_path["folder_path"] = body["folder_path"]
    project = projects_data.update_project(
        conn,
        project_id,
        name=body.get("name"),
        summary=body.get("summary"),
        priority=body.get("priority"),
        now=clk.now_unix(),
        **folder_path,
    )
    return projects_data.project_json(project)
