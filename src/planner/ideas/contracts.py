"""Idea contracts."""

from dataclasses import dataclass
from typing import TypedDict


class CreateIdeaBody(TypedDict, total=False):  # POST /ideas
    title: str  # route requires it non-empty
    body: str  # default ""
    project: str | None  # legacy project name
    project_id: str | None


@dataclass
class Idea:  # §3.5
    id: str
    title: str
    body: str
    project_id: str | None
    project_name: str | None
    created_at: int
    updated_at: int
