"""Project catalog contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from planner.core.contracts import Priority


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    summary: str
    priority: Priority | None
    created_at: int
    updated_at: int


class CreateProjectBody(TypedDict):
    name: str
    summary: str
    priority: Priority


class UpdateProjectBody(TypedDict, total=False):
    name: str
    summary: str
    priority: Priority
