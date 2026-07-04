"""Latest-daily selection: pick the lexicographically greatest
``daily/YYYY-MM-DD/`` folder that contains a workspace.md. Pure."""

from __future__ import annotations

import re
from collections.abc import Sequence

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def pick_latest_daily(folders: Sequence[tuple[str, bool]]) -> tuple[str | None, list[str]]:
    """Input: (folder_name, has_workspace_md) pairs. Returns (chosen, skipped)
    where skipped is every other folder name, ascending."""
    candidates = [name for name, has_ws in folders if has_ws and _DATE_RE.match(name)]
    chosen = max(candidates) if candidates else None
    skipped = sorted(name for name, _ in folders if name != chosen)
    return chosen, skipped
