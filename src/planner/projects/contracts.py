"""Project catalog contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    summary: str
    folder_path: Path | None
    created_at: int
    updated_at: int
    requires_macos: bool = False


class CreateProjectBody(TypedDict):
    name: str
    summary: str
    requires_macos: NotRequired[bool]
    folder_path: NotRequired[str | None]


class UpdateProjectBody(TypedDict, total=False):
    requires_macos: bool
    name: str
    summary: str
    folder_path: str | None
