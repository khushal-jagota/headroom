"""Process-private Claude input records; never serialize these values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeJsonValue:
    kind: str
    value: str | bool | int | float | None | tuple[ClaudeJsonValue, ...] | tuple[
        tuple[str, ClaudeJsonValue], ...
    ]


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeInputLocation:
    logical: Path
    resolved: Path


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeInputDocument:
    role: str
    location: ClaudeInputLocation
    content: ClaudeJsonValue | None


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeInputDirectory:
    location: ClaudeInputLocation
    candidates: tuple[Path, ...]


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeCredentialMarker:
    location: ClaudeInputLocation
    resolved_parent: Path
    metadata: tuple[int, ...] | None


@dataclass(frozen=True, slots=True, repr=False)
class ClaudeInputVector:
    global_selection: str
    documents: tuple[ClaudeInputDocument, ...]
    directories: tuple[ClaudeInputDirectory, ...]
    credential: ClaudeCredentialMarker
