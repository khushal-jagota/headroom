"""Contracts for managed files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TicketFile:
    ticket_id: str
    relative_path: str
    absolute_path: Path


@dataclass(frozen=True)
class SprintItemFile:
    sprint_item_id: str
    relative_path: str
    absolute_path: Path


@dataclass(frozen=True)
class ArtifactEntry:
    """One thing a reader would open in a folder of managed files.

    A file is itself. A directory that holds an index is that index, and nothing inside it
    is listed, because the files around an index are what the index loads. A directory with
    no index has no single page to open, so it carries what is directly inside it, and the
    same rule applies there.
    """

    name: str
    opens: str | None
    modified_at: float
    children: tuple[ArtifactEntry, ...]
