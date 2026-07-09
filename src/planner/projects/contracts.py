"""Project catalog contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    created_at: int
    updated_at: int


class CreateProjectBody(TypedDict, total=False):
    name: str
