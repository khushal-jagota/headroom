"""Project catalog contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    summary: str
    created_at: int
    updated_at: int


class CreateProjectBody(TypedDict):
    name: str
    summary: str


class UpdateProjectBody(TypedDict, total=False):
    name: str
    summary: str
