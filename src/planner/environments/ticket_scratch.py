"""Locate attributable legacy scratch; the Ticket workspace clock owns removal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SCRATCH_FOLDERS = ("task-tmp", "worktrees", "repository-worktrees")
LEGACY_HOME_FOLDERS = ("pct", "panels-session-evidence")
CLEANUP_INTERVAL_SECONDS = 600


@dataclass(frozen=True)
class ScratchLayout:
    repository: Path
    folders: tuple[Path, ...]

    @classmethod
    def under(cls, root: Path, home: Path | None = None) -> ScratchLayout:
        root = root.resolve()
        home = (Path.home() if home is None else home).resolve()
        return cls(
            repository=root / "repository",
            folders=tuple(root / name for name in SCRATCH_FOLDERS)
            + tuple(home / name for name in LEGACY_HOME_FOLDERS),
        )


def layout_for(root: Path | None, database: str | None, db_path: str) -> ScratchLayout | None:
    """The layout, only for the one database that the scratch belongs to.

    Every other database would count the live Tickets as deleted. A Ticket server can
    inherit the live configuration, so the database path decides, not the configuration.
    """
    if root is None or database is None:
        return None
    if Path(database).expanduser().resolve() != Path(db_path).expanduser().resolve():
        return None
    return ScratchLayout.under(root)

