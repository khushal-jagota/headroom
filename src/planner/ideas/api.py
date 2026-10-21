"""Idea HTTP routes."""

from typing import Any

from fastapi import APIRouter

from planner.core.contracts import JsonDict
from planner.ideas import data as ideas_data
from planner.ideas import views as ideas_views
from planner.ideas.contracts import CreateIdeaBody
from planner.projects import data as projects_data
from planner.tickets.api import Clk, DbConn, body_opt_str, body_str

router = APIRouter()


def _marshal_create_idea(raw: JsonDict) -> CreateIdeaBody:
    return CreateIdeaBody(
        title=body_str(raw, "title"),
        body=body_str(raw, "body"),
        project=body_opt_str(raw, "project"),
        project_id=body_opt_str(raw, "project_id"),
    )


@router.post("/ideas")
async def create_idea(raw: dict[str, Any], conn: DbConn, clk: Clk) -> JsonDict:
    body = _marshal_create_idea(raw)
    project = projects_data.resolve_project(
        conn, project_id=body["project_id"], project_name=body["project"]
    )
    idea = ideas_data.create_idea(
        conn,
        title=body["title"],
        body=body["body"],
        project_id=project.id if project is not None else None,
        now=clk.now_unix(),
    )
    return ideas_views.idea_json(idea)


@router.get("/ideas")
async def list_ideas(
    conn: DbConn, project: str | None = None, project_id: str | None = None
) -> JsonDict:
    resolved_project = projects_data.resolve_project(
        conn, project_id=project_id, project_name=project
    )
    return {
        "ideas": ideas_views.list_ideas(
            conn,
            project_id=resolved_project.id if resolved_project is not None else None,
        )
    }
