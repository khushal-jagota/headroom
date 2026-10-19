"""The rule that turns a directory of managed files into the things a reader would open."""

from __future__ import annotations

import os
from pathlib import Path


def _write(root: Path, relative_path: str, *, modified_at: float | None = None) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(relative_path, encoding="utf-8")
    if modified_at is not None:
        os.utime(path, (modified_at, modified_at))
    return path
